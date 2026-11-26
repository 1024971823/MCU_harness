#include "controllers/WorkbenchController.h"
#include "app/AppInfo.h"
#include "backend/EngineClient.h"
#include "controllers/DemoSeeder.h"
#include "core/AppSettings.h"
#include "core/DiagnosticsService.h"
#include "core/UpdateManager.h"
#include "models/AuditModel.h"
#include "models/ConversationModel.h"
#include "models/LogModel.h"
#include "models/PlaybookModel.h"
#include "models/RtosTaskModel.h"
#include "models/SessionModel.h"
#include "models/WatchModel.h"

#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QTimer>
#include <QUrl>

namespace {

QList<LogModel::Entry> parseLogEntriesArray(const QJsonArray &arr) {
    QList<LogModel::Entry> out;
    out.reserve(arr.size());
    for (const QJsonValue &v : arr) {
        const QJsonObject obj = v.toObject();
        LogModel::Entry e;
        e.seq = static_cast<quint64>(obj.value(QStringLiteral("seq")).toInteger(0));
        e.timestamp = obj.value(QStringLiteral("timestamp")).toDouble(0.0);
        e.text = obj.value(QStringLiteral("text")).toString();
        out.append(std::move(e));
    }
    return out;
}

} // namespace

WorkbenchController::WorkbenchController(AppSettings *settings,
                                         AppInfo *appInfo,
                                         UpdateManager *updater,
                                         DiagnosticsService *diagnostics,
                                         bool demoMode,
                                         QObject *parent)
    : QObject(parent)
    , m_settings(settings)
    , m_appInfo(appInfo)
    , m_updater(updater)
    , m_diagnostics(diagnostics)
    , m_demoMode(demoMode)
    , m_engine(new EngineClient(this))
    , m_conversation(new ConversationModel(this))
    , m_playbooks(new PlaybookModel(this))
    , m_sessions(new SessionModel(this))
    , m_rttLogs(new LogModel(20000, this))
    , m_uartLogs(new LogModel(20000, this))
    , m_engineLogs(new LogModel(20000, this))
    , m_watch(new WatchModel(this))
    , m_rtosTasks(new RtosTaskModel(this))
    , m_rtosHazards(new RtosHazardModel(this))
    , m_audit(new AuditModel(this))
    , m_pollTimer(new QTimer(this)) {
    if (m_diagnostics) {
        m_diagnostics->setEngineClient(m_engine);
    }

    if (m_settings) {
        m_provider = m_settings->provider();
        m_model = m_settings->model();
        m_interlockMode = m_settings->interlockMode();
        m_watchExprs = m_settings->watchExpressions();

        QVariantMap t;
        t.insert(QStringLiteral("projectDir"), m_settings->projectDir());
        m_target = t;

        connect(m_settings, &AppSettings::telemetryIntervalMsChanged, this, &WorkbenchController::updatePollingRate);
        connect(m_settings, &AppSettings::autoRefreshChanged, this, &WorkbenchController::updatePollingRate);
        connect(m_settings, &AppSettings::languageChanged, this, [this]() {
            m_playbooks->refreshLocalization();
            m_sessions->refreshLocalization();
        });
    }

    connect(m_engine, &EngineClient::started, this, &WorkbenchController::onEngineStarted);
    connect(m_engine, &EngineClient::readyReceived, this, &WorkbenchController::onEngineReady);
    connect(m_engine, &EngineClient::eventReceived, this, &WorkbenchController::onEngineEvent);
    connect(m_engine, &EngineClient::stderrLine, this, &WorkbenchController::onEngineStderr);
    connect(m_engine, &EngineClient::exited, this, &WorkbenchController::onEngineExited);
    connect(m_engine, &EngineClient::errorOccurred, this, &WorkbenchController::onEngineError);

    connect(m_pollTimer, &QTimer::timeout, this, &WorkbenchController::onPollTimerTick);
    updatePollingRate();
}

WorkbenchController::~WorkbenchController() {
    shutdownEngine();
}

QObject *WorkbenchController::messagesModel() const { return m_conversation; }
QObject *WorkbenchController::playbooksModel() const { return m_playbooks; }
QObject *WorkbenchController::sessionsModel() const { return m_sessions; }
QObject *WorkbenchController::rttLogModel() const { return m_rttLogs; }
QObject *WorkbenchController::uartLogModel() const { return m_uartLogs; }
QObject *WorkbenchController::engineLogModel() const { return m_engineLogs; }
QObject *WorkbenchController::watchModel() const { return m_watch; }
QObject *WorkbenchController::rtosTasksModel() const { return m_rtosTasks; }
QObject *WorkbenchController::rtosHazardsModel() const { return m_rtosHazards; }
QObject *WorkbenchController::auditModel() const { return m_audit; }

void WorkbenchController::setTargetMap(const QVariantMap &m) {
    m_target = m;
    emit telemetryChanged();
}

void WorkbenchController::setProbeMap(const QVariantMap &m) {
    m_probe = m;
    emit telemetryChanged();
}

void WorkbenchController::setRttMap(const QVariantMap &m) {
    m_rtt = m;
    emit telemetryChanged();
}

void WorkbenchController::setUartMap(const QVariantMap &m) {
    m_uart = m;
    emit telemetryChanged();
}

void WorkbenchController::setRtosMap(const QVariantMap &m) {
    m_rtos = m;
    emit telemetryChanged();
}

void WorkbenchController::setStatusCardText(const QString &md) {
    m_statusCard = md;
    emit telemetryChanged();
}

void WorkbenchController::setTokenUsageText(const QString &text) {
    if (m_tokenUsageText != text) {
        m_tokenUsageText = text;
        emit tokenUsageChanged();
    }
}

void WorkbenchController::setEngineReadyDemo(const QString &provider,
                                             const QString &model,
                                             const QString &mode,
                                             const QString &sessionId) {
    m_provider = provider;
    m_model = model;
    m_interlockMode = mode;
    m_sessionId = sessionId;
    setEngineStatus(QStringLiteral("ready"));
    emit configChanged();
    emit sessionChanged();
}

void WorkbenchController::setEngineStatus(const QString &status, const QString &error) {
    if (m_engineStatus != status || m_engineError != error) {
        m_engineStatus = status;
        m_engineError = error;
        emit engineStatusChanged();
    }
}

void WorkbenchController::startEngine() {
    EngineClient::LaunchConfig cfg;
    if (m_settings) {
        cfg.projectDir = m_settings->projectDir().isEmpty() ? QDir::currentPath() : m_settings->projectDir();
        cfg.provider = m_settings->provider();
        cfg.model = m_settings->model();
        cfg.baseUrl = m_settings->baseUrl();
        cfg.apiKey = m_settings->apiKey();
        cfg.mode = m_settings->interlockMode();
        cfg.mockLlm = m_settings->mockLlm() || m_demoMode;
    } else {
        cfg.projectDir = QDir::currentPath();
        cfg.mockLlm = m_demoMode;
    }

    setEngineStatus(QStringLiteral("starting"));
    m_engine->start(cfg);
}

void WorkbenchController::shutdownEngine() {
    m_pollTimer->stop();
    m_engine->shutdown();
    setEngineStatus(QStringLiteral("stopped"));
}

void WorkbenchController::restartEngine() {
    m_demoSeeded = false;
    setEngineStatus(QStringLiteral("starting"));
    m_engine->restart();
}

void WorkbenchController::setWindowMinimized(bool minimized) {
    if (m_windowMinimized != minimized) {
        m_windowMinimized = minimized;
        updatePollingRate();
    }
}

void WorkbenchController::updatePollingRate() {
    if (m_settings && !m_settings->autoRefresh()) {
        m_pollTimer->stop();
        return;
    }
    const int baseMs = m_settings ? m_settings->telemetryIntervalMs() : 1000;
    const int interval = (m_windowMinimized || m_turnRunning) ? qMax(baseMs, 5000) : baseMs;
    m_pollTimer->setInterval(interval);
    if (m_engine->isRunning() && !m_pollTimer->isActive()) {
        m_pollTimer->start();
    }
}

QVariantMap WorkbenchController::currentApproval() const {
    if (m_pendingApprovals.isEmpty()) {
        return {};
    }
    const ApprovalItem &item = m_pendingApprovals.first();
    QVariantMap m;
    m.insert(QStringLiteral("requestId"), item.requestId);
    m.insert(QStringLiteral("toolName"), item.toolName);
    m.insert(QStringLiteral("riskLevel"), item.riskLevel);
    m.insert(QStringLiteral("summary"), item.summary);
    m.insert(QStringLiteral("detailsJson"), item.detailsJson);
    m.insert(QStringLiteral("diffPreview"), item.diffPreview);
    m.insert(QStringLiteral("svdPreview"), item.svdPreview);
    m.insert(QStringLiteral("isHardwareDanger"), item.isHardwareDanger);
    return m;
}

void WorkbenchController::enqueueApproval(const QJsonObject &reqObj) {
    const QString reqId = reqObj.value(QStringLiteral("request_id")).toString();
    if (reqId.isEmpty()) {
        return;
    }
    for (const ApprovalItem &existing : m_pendingApprovals) {
        if (existing.requestId == reqId) {
            return;
        }
    }

    ApprovalItem item;
    item.requestId = reqId;
    item.toolName = reqObj.value(QStringLiteral("tool_name")).toString();
    item.riskLevel = reqObj.value(QStringLiteral("risk_level")).toString(QStringLiteral("hardware_state"));
    item.summary = reqObj.value(QStringLiteral("summary")).toString();
    item.detailsJson = QString::fromUtf8(
        QJsonDocument(reqObj.value(QStringLiteral("details")).toObject()).toJson(QJsonDocument::Indented));
    item.diffPreview = reqObj.value(QStringLiteral("diff_preview")).toString();
    item.svdPreview = reqObj.value(QStringLiteral("svd_preview")).toString();
    item.isHardwareDanger = (item.riskLevel == QStringLiteral("hardware_danger") ||
                             item.toolName == QStringLiteral("flash_firmware") ||
                             item.toolName == QStringLiteral("write_memory") ||
                             item.toolName == QStringLiteral("run_hil_tests"));

    m_pendingApprovals.append(std::move(item));
    emit pendingApprovalsChanged();
}

void WorkbenchController::removeApprovalById(const QString &reqId) {
    for (int i = 0; i < m_pendingApprovals.size(); ++i) {
        if (m_pendingApprovals.at(i).requestId == reqId) {
            m_pendingApprovals.removeAt(i);
            emit pendingApprovalsChanged();
            return;
        }
    }
}

void WorkbenchController::onEngineStarted() {
    m_engineLogs->appendEntry(0, 0.0, QStringLiteral("[studio] Python RPC engine started."), QStringLiteral("info"));
}

void WorkbenchController::onEngineReady(const QJsonObject &data) {
    m_sessionId = data.value(QStringLiteral("session_id")).toString();
    m_provider = data.value(QStringLiteral("provider")).toString();
    m_model = data.value(QStringLiteral("model")).toString();
    m_interlockMode = data.value(QStringLiteral("interlock_mode")).toString(QStringLiteral("interactive"));

    setEngineStatus(QStringLiteral("ready"));
    emit configChanged();
    emit sessionChanged();

    m_engine->sendRequest(QStringLiteral("ping"), QJsonObject(), [this](bool ok, const QJsonObject &res, const QString &) {
        if (ok && m_appInfo && res.contains(QStringLiteral("version"))) {
            m_appInfo->setEngineVersion(res.value(QStringLiteral("version")).toString());
        }
    });

    m_engine->sendRequest(QStringLiteral("playbooks"), QJsonObject(), [this](bool ok, const QJsonObject &res, const QString &) {
        if (ok) {
            m_playbooks->updatePlaybooks(res.value(QStringLiteral("playbooks")).toArray());
        }
    });

    refreshSessions();
    if (!m_demoSeeded) {
        refreshSnapshot();
    }
    updatePollingRate();
}

void WorkbenchController::onEngineEvent(const QString &kind, quint64 seq, double timestamp, const QJsonObject &data) {
    Q_UNUSED(seq);
    Q_UNUSED(timestamp);

    if (kind == QStringLiteral("turn_started")) {
        if (!m_turnRunning) {
            m_turnRunning = true;
            emit turnRunningChanged();
            updatePollingRate();
        }
        m_conversation->startAssistantMessage();
    } else if (kind == QStringLiteral("text_delta")) {
        m_conversation->appendAssistantDelta(data.value(QStringLiteral("delta")).toString());
    } else if (kind == QStringLiteral("tool_call")) {
        m_conversation->addToolCall(
            data.value(QStringLiteral("id")).toString(),
            data.value(QStringLiteral("name")).toString(),
            data.value(QStringLiteral("arguments")).toObject());
    } else if (kind == QStringLiteral("tool_result")) {
        m_conversation->upsertToolResult(
            data.value(QStringLiteral("id")).toString(),
            data.value(QStringLiteral("name")).toString(),
            data.value(QStringLiteral("ok")).toBool(false),
            data.value(QStringLiteral("denied")).toBool(false),
            data.value(QStringLiteral("hard_denied")).toBool(false),
            data.value(QStringLiteral("result")).toObject(),
            data.value(QStringLiteral("error")).toString());
    } else if (kind == QStringLiteral("approval_required")) {
        enqueueApproval(data);
    } else if (kind == QStringLiteral("approval_resolved")) {
        removeApprovalById(data.value(QStringLiteral("request_id")).toString());
    } else if (kind == QStringLiteral("turn_completed")) {
        m_conversation->finishAssistantMessage(data.value(QStringLiteral("text")).toString());
        if (m_turnRunning) {
            m_turnRunning = false;
            emit turnRunningChanged();
            updatePollingRate();
        }
        const QJsonObject usage = data.value(QStringLiteral("usage")).toObject();
        const int totalTok = usage.value(QStringLiteral("total_tokens")).toInt(0);
        const int promptTok = usage.value(QStringLiteral("prompt_tokens")).toInt(0);
        const int compTok = usage.value(QStringLiteral("completion_tokens")).toInt(0);
        if (totalTok > 0) {
            setTokenUsageText(QStringLiteral("tokens: %1 (%2 in / %3 out)").arg(totalTok).arg(promptTok).arg(compTok));
        }
        const QString err = data.value(QStringLiteral("error")).toString();
        if (!err.isEmpty()) {
            m_conversation->addNotice(QStringLiteral("error"), err);
        }
        refreshSnapshot();
        refreshSessions();
    } else if (kind == QStringLiteral("turn_error")) {
        m_conversation->finishAssistantMessage();
        m_conversation->addNotice(QStringLiteral("error"), data.value(QStringLiteral("error")).toString());
        if (m_turnRunning) {
            m_turnRunning = false;
            emit turnRunningChanged();
            updatePollingRate();
        }
    }
}

void WorkbenchController::onEngineStderr(const QString &line) {
    m_engineLogs->appendEntry(0, 0.0, line);
}

void WorkbenchController::onEngineExited(int exitCode, const QString &reason) {
    Q_UNUSED(exitCode);
    if (m_turnRunning) {
        m_turnRunning = false;
        emit turnRunningChanged();
    }
    m_pollTimer->stop();
    setEngineStatus(QStringLiteral("error"), reason);
    m_engineLogs->appendEntry(0, 0.0, reason, QStringLiteral("error"));
}

void WorkbenchController::onEngineError(const QString &error) {
    setEngineStatus(QStringLiteral("error"), error);
    m_engineLogs->appendEntry(0, 0.0, error, QStringLiteral("error"));
}

void WorkbenchController::onPollTimerTick() {
    if (!m_engine->isRunning() || m_demoSeeded) {
        return;
    }
    refreshSnapshot();
    pollLogsIncremental(QStringLiteral("rtt"));
    pollLogsIncremental(QStringLiteral("uart"));
}

void WorkbenchController::refreshSnapshot() {
    if (!m_engine->isRunning() || m_telemetryInFlight) {
        return;
    }
    m_telemetryInFlight = true;
    QJsonObject params;
    params.insert(QStringLiteral("watch_exprs"), QJsonArray::fromStringList(m_watchExprs));

    m_engine->sendRequest(
        QStringLiteral("telemetry"),
        params,
        [this](bool ok, const QJsonObject &res, const QString &) {
            m_telemetryInFlight = false;
            if (ok) {
                applyTelemetrySnapshot(res);
            }
        },
        8000);
}

void WorkbenchController::pollLogsIncremental(const QString &source) {
    if (!m_engine->isRunning()) {
        return;
    }
    const bool isRtt = (source == QStringLiteral("rtt"));
    if (isRtt && m_rttLogsInFlight) {
        return;
    }
    if (!isRtt && m_uartLogsInFlight) {
        return;
    }
    if (isRtt) {
        m_rttLogsInFlight = true;
    } else {
        m_uartLogsInFlight = true;
    }

    const quint64 afterSeq = isRtt ? m_rttLogs->lastSeq() : m_uartLogs->lastSeq();
    QJsonObject params;
    params.insert(QStringLiteral("source"), source);
    params.insert(QStringLiteral("after_seq"), static_cast<qint64>(afterSeq));
    params.insert(QStringLiteral("limit"), 2000);

    m_engine->sendRequest(
        QStringLiteral("logs_since"),
        params,
        [this, isRtt](bool ok, const QJsonObject &res, const QString &) {
            if (isRtt) {
                m_rttLogsInFlight = false;
            } else {
                m_uartLogsInFlight = false;
            }
            if (!ok) {
                return;
            }
            const QList<LogModel::Entry> entries = parseLogEntriesArray(res.value(QStringLiteral("entries")).toArray());
            if (!entries.isEmpty()) {
                if (isRtt) {
                    m_rttLogs->appendEntries(entries);
                } else {
                    m_uartLogs->appendEntries(entries);
                }
            }
        },
        5000);
}

void WorkbenchController::applyTelemetrySnapshot(const QJsonObject &snap) {
    if (snap.isEmpty()) {
        return;
    }

    const QString newSession = snap.value(QStringLiteral("session_id")).toString();
    if (!newSession.isEmpty() && newSession != m_sessionId) {
        m_sessionId = newSession;
        m_sessions->setCurrentSessionId(m_sessionId);
        emit sessionChanged();
    }

    const QString newProv = snap.value(QStringLiteral("provider")).toString();
    const QString newModel = snap.value(QStringLiteral("model")).toString();
    const QString newMode = snap.value(QStringLiteral("interlock_mode")).toString();
    if ((!newProv.isEmpty() && newProv != m_provider) ||
        (!newModel.isEmpty() && newModel != m_model) ||
        (!newMode.isEmpty() && newMode != m_interlockMode)) {
        if (!newProv.isEmpty()) m_provider = newProv;
        if (!newModel.isEmpty()) m_model = newModel;
        if (!newMode.isEmpty()) m_interlockMode = newMode;
        emit configChanged();
    }

    m_statusCard = snap.value(QStringLiteral("status_card")).toString();

    const QJsonObject tel = snap.value(QStringLiteral("telemetry")).toObject();
    const QJsonObject artifacts = tel.value(QStringLiteral("artifacts")).toObject();
    const QJsonObject elfObj = artifacts.value(QStringLiteral("elf")).toObject();
    const QJsonObject svdObj = artifacts.value(QStringLiteral("svd")).toObject();
    const QJsonObject iocObj = artifacts.value(QStringLiteral("ioc")).toObject();

    QVariantMap targetMap;
    targetMap.insert(QStringLiteral("projectDir"), tel.value(QStringLiteral("project_dir")).toString());
    targetMap.insert(QStringLiteral("device"), tel.value(QStringLiteral("device")).toString());
    targetMap.insert(QStringLiteral("core"), tel.value(QStringLiteral("core")).toString());
    targetMap.insert(QStringLiteral("buildSystem"), tel.value(QStringLiteral("build_system")).toString());
    targetMap.insert(QStringLiteral("elfPath"), elfObj.value(QStringLiteral("path")).toString());
    targetMap.insert(QStringLiteral("elfExists"), elfObj.value(QStringLiteral("exists")).toBool(false));
    targetMap.insert(QStringLiteral("svdPath"), svdObj.value(QStringLiteral("path")).toString());
    targetMap.insert(QStringLiteral("svdExists"), svdObj.value(QStringLiteral("exists")).toBool(false));
    targetMap.insert(QStringLiteral("iocPath"), iocObj.value(QStringLiteral("path")).toString());
    targetMap.insert(QStringLiteral("iocExists"), iocObj.value(QStringLiteral("exists")).toBool(false));
    m_target = targetMap;

    const QJsonObject probeObj = tel.value(QStringLiteral("probe")).toObject();
    QVariantMap probeMap;
    probeMap.insert(QStringLiteral("connected"), probeObj.value(QStringLiteral("connected")).toBool(false));
    if (probeObj.value(QStringLiteral("halted")).isBool()) {
        probeMap.insert(QStringLiteral("halted"), probeObj.value(QStringLiteral("halted")).toBool());
    }
    probeMap.insert(QStringLiteral("device"), probeObj.value(QStringLiteral("device")).toString());
    probeMap.insert(QStringLiteral("backend"), probeObj.value(QStringLiteral("backend")).toString());
    probeMap.insert(QStringLiteral("interface"), probeObj.value(QStringLiteral("interface")).toString());
    probeMap.insert(QStringLiteral("serialNo"), probeObj.value(QStringLiteral("serial_no")).toString());
    probeMap.insert(QStringLiteral("error"), probeObj.value(QStringLiteral("error")).toString());
    m_probe = probeMap;

    const QJsonObject tlogs = tel.value(QStringLiteral("logs")).toObject();
    const QJsonObject rttTel = tlogs.value(QStringLiteral("rtt")).toObject();
    QVariantMap rttMap;
    rttMap.insert(QStringLiteral("running"), rttTel.value(QStringLiteral("running")).toBool(false));
    rttMap.insert(QStringLiteral("channel"), rttTel.value(QStringLiteral("channel")).toInt(0));
    rttMap.insert(QStringLiteral("bufferedLines"), rttTel.value(QStringLiteral("buffered_lines")).toInt(0));
    rttMap.insert(QStringLiteral("bytesReceived"), rttTel.value(QStringLiteral("bytes_received")).toInteger(0));
    rttMap.insert(QStringLiteral("lastError"), rttTel.value(QStringLiteral("last_error")).toString());
    m_rtt = rttMap;

    const QJsonObject uartTel = tlogs.value(QStringLiteral("uart")).toObject();
    QVariantMap uartMap;
    uartMap.insert(QStringLiteral("running"), uartTel.value(QStringLiteral("running")).toBool(false));
    uartMap.insert(QStringLiteral("port"), uartTel.value(QStringLiteral("port")).toString());
    uartMap.insert(QStringLiteral("baudrate"), uartTel.value(QStringLiteral("baudrate")).toInt(115200));
    uartMap.insert(QStringLiteral("bufferedLines"), uartTel.value(QStringLiteral("buffered_lines")).toInt(0));
    uartMap.insert(QStringLiteral("bytesReceived"), uartTel.value(QStringLiteral("bytes_received")).toInteger(0));
    uartMap.insert(QStringLiteral("lastError"), uartTel.value(QStringLiteral("last_error")).toString());
    m_uart = uartMap;

    const QJsonObject logsSection = snap.value(QStringLiteral("logs")).toObject();
    const QJsonArray rttEntries = logsSection.value(QStringLiteral("rtt")).toObject().value(QStringLiteral("entries")).toArray();
    if (!rttEntries.isEmpty() && m_rttLogs->rowCount() == 0) {
        m_rttLogs->appendEntries(parseLogEntriesArray(rttEntries));
    }
    const QJsonArray uartEntries = logsSection.value(QStringLiteral("uart")).toObject().value(QStringLiteral("entries")).toArray();
    if (!uartEntries.isEmpty() && m_uartLogs->rowCount() == 0) {
        m_uartLogs->appendEntries(parseLogEntriesArray(uartEntries));
    }

    const QJsonArray watchExprsJson = snap.value(QStringLiteral("watch_exprs")).toArray();
    QStringList exprList = m_watchExprs;
    if (!watchExprsJson.isEmpty()) {
        exprList.clear();
        for (const QJsonValue &v : watchExprsJson) {
            exprList.append(v.toString());
        }
    }
    m_watch->updateFromSnapshot(snap.value(QStringLiteral("watch")).toObject(), exprList);

    const QJsonValue rtosVal = snap.value(QStringLiteral("rtos"));
    if (rtosVal.isObject()) {
        const QJsonObject ro = rtosVal.toObject();
        QVariantMap rtosMap;
        rtosMap.insert(QStringLiteral("rtos"), ro.value(QStringLiteral("rtos")).toString());
        rtosMap.insert(QStringLiteral("elfPath"), ro.value(QStringLiteral("elf_path")).toString());
        if (!ro.value(QStringLiteral("tick_count")).isNull()) {
            rtosMap.insert(QStringLiteral("tickCount"), ro.value(QStringLiteral("tick_count")).toInteger(0));
        }
        if (ro.value(QStringLiteral("scheduler_running")).isBool()) {
            rtosMap.insert(QStringLiteral("schedulerRunning"), ro.value(QStringLiteral("scheduler_running")).toBool());
        }
        const QString curTask = ro.value(QStringLiteral("current_task")).toString();
        rtosMap.insert(QStringLiteral("currentTask"), curTask);
        rtosMap.insert(QStringLiteral("error"), ro.value(QStringLiteral("error")).toString());
        m_rtos = rtosMap;
        m_rtosTasks->updateTasks(ro.value(QStringLiteral("tasks")).toArray(), curTask);
        m_rtosHazards->updateHazards(ro.value(QStringLiteral("hazards")).toArray());
    } else {
        m_rtos.clear();
        m_rtosTasks->clear();
        m_rtosHazards->clear();
    }

    const QJsonArray auditArr = snap.contains(QStringLiteral("audit"))
                                    ? snap.value(QStringLiteral("audit")).toArray()
                                    : tel.value(QStringLiteral("recent_audit")).toArray();
    m_audit->updateEntries(auditArr);

    emit telemetryChanged();
}

void WorkbenchController::sendMessage(const QString &text) {
    const QString trimmed = text.trimmed();
    if (trimmed.isEmpty() || m_turnRunning) {
        return;
    }
    m_demoSeeded = false;
    m_conversation->appendUserMessage(trimmed);

    if (!m_engine->isRunning()) {
        m_conversation->addNotice(QStringLiteral("error"), tr("Backend engine is not running."));
        return;
    }

    m_turnRunning = true;
    emit turnRunningChanged();
    updatePollingRate();

    QJsonObject params;
    params.insert(QStringLiteral("message"), trimmed);
    m_engine->sendRequest(QStringLiteral("chat"), params, [this](bool ok, const QJsonObject &, const QString &err) {
        if (!ok) {
            if (m_turnRunning) {
                m_turnRunning = false;
                emit turnRunningChanged();
                updatePollingRate();
            }
            m_conversation->addNotice(QStringLiteral("error"), err);
        }
    });
}

void WorkbenchController::abortTurn() {
    if (!m_engine->isRunning()) {
        return;
    }
    m_engine->sendRequest(QStringLiteral("abort"), QJsonObject(), [this](bool, const QJsonObject &, const QString &) {
        if (m_turnRunning) {
            m_turnRunning = false;
            emit turnRunningChanged();
            updatePollingRate();
        }
    });
}

void WorkbenchController::resolveApproval(const QString &requestId,
                                          bool approved,
                                          const QString &scope,
                                          const QString &reason) {
    removeApprovalById(requestId);
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("request_id"), requestId);
    params.insert(QStringLiteral("approved"), approved);
    params.insert(QStringLiteral("scope"), approved ? scope : QStringLiteral("deny"));
    params.insert(QStringLiteral("reason"), reason);
    m_engine->sendRequest(QStringLiteral("resolve_approval"), params);
}

void WorkbenchController::setInterlockMode(const QString &mode) {
    const QString norm = mode.trimmed().toLower();
    if (m_settings) {
        m_settings->setInterlockMode(norm);
    }
    m_interlockMode = norm;
    emit configChanged();

    if (m_engine->isRunning()) {
        QJsonObject params;
        params.insert(QStringLiteral("mode"), norm);
        m_engine->sendRequest(QStringLiteral("set_mode"), params);
    }
}

void WorkbenchController::openProject(const QString &pathOrUrl) {
    QString path = pathOrUrl.trimmed();
    const QUrl url(path);
    if (url.isLocalFile()) {
        path = url.toLocalFile();
    }
    if (path.isEmpty()) {
        return;
    }
    path = QDir::cleanPath(path);
    if (m_settings) {
        m_settings->setProjectDir(path);
        m_settings->addRecentProject(path);
    }
    m_target.insert(QStringLiteral("projectDir"), path);
    emit telemetryChanged();

    if (m_engine->isRunning()) {
        QJsonObject params;
        params.insert(QStringLiteral("project_dir"), path);
        m_engine->sendRequest(QStringLiteral("configure"), params, [this](bool ok, const QJsonObject &, const QString &) {
            if (ok) {
                refreshSnapshot();
                refreshSessions();
            }
        });
    }
}

void WorkbenchController::newSession() {
    m_conversation->clear();
    setTokenUsageText(QString());
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("new"));
    m_engine->sendRequest(QStringLiteral("sessions"), params, [this](bool ok, const QJsonObject &res, const QString &) {
        if (ok) {
            m_sessionId = res.value(QStringLiteral("session_id")).toString();
            m_sessions->setCurrentSessionId(m_sessionId);
            emit sessionChanged();
            refreshSessions();
        }
    });
}

void WorkbenchController::loadSession(const QString &sessionId) {
    if (sessionId.isEmpty() || !m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("load"));
    params.insert(QStringLiteral("session_id"), sessionId);
    m_engine->sendRequest(QStringLiteral("sessions"), params, [this](bool ok, const QJsonObject &res, const QString &err) {
        if (!ok) {
            m_conversation->addNotice(QStringLiteral("error"), err);
            return;
        }
        m_sessionId = res.value(QStringLiteral("session_id")).toString();
        m_sessions->setCurrentSessionId(m_sessionId);
        m_conversation->loadFromSessionMessages(res.value(QStringLiteral("messages")).toArray());
        emit sessionChanged();
    });
}

void WorkbenchController::refreshSessions() {
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("list"));
    m_engine->sendRequest(QStringLiteral("sessions"), params, [this](bool ok, const QJsonObject &res, const QString &) {
        if (ok) {
            const QString cur = res.value(QStringLiteral("current_session_id")).toString(m_sessionId);
            m_sessions->updateSessions(res.value(QStringLiteral("sessions")).toArray(), cur);
        }
    });
}

void WorkbenchController::addWatch(const QString &expr) {
    const QString cleaned = expr.trimmed();
    if (cleaned.isEmpty()) {
        return;
    }
    if (!m_watchExprs.contains(cleaned)) {
        m_watchExprs.append(cleaned);
        if (m_settings) {
            m_settings->setWatchExpressions(m_watchExprs);
        }
    }
    if (m_engine->isRunning()) {
        QJsonObject params;
        params.insert(QStringLiteral("action"), QStringLiteral("add"));
        params.insert(QStringLiteral("expression"), cleaned);
        m_engine->sendRequest(QStringLiteral("watch"), params, [this](bool ok, const QJsonObject &, const QString &) {
            if (ok) {
                refreshSnapshot();
            }
        });
    }
}

void WorkbenchController::removeWatch(const QString &expr) {
    m_watchExprs.removeAll(expr);
    if (m_settings) {
        m_settings->setWatchExpressions(m_watchExprs);
    }
    if (m_engine->isRunning()) {
        QJsonObject params;
        params.insert(QStringLiteral("action"), QStringLiteral("remove"));
        params.insert(QStringLiteral("expression"), expr);
        m_engine->sendRequest(QStringLiteral("watch"), params, [this](bool ok, const QJsonObject &, const QString &) {
            if (ok) {
                refreshSnapshot();
            }
        });
    }
}

void WorkbenchController::clearWatch() {
    m_watchExprs.clear();
    if (m_settings) {
        m_settings->setWatchExpressions(m_watchExprs);
    }
    m_watch->clear();
    if (m_engine->isRunning()) {
        QJsonObject params;
        params.insert(QStringLiteral("action"), QStringLiteral("clear"));
        m_engine->sendRequest(QStringLiteral("watch"), params);
    }
}

void WorkbenchController::probeConnect(const QString &backend) {
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("connect"));
    if (!backend.trimmed().isEmpty()) {
        params.insert(QStringLiteral("backend"), backend.trimmed());
    }
    m_engine->sendRequest(QStringLiteral("probe"), params, [this](bool, const QJsonObject &, const QString &) {
        refreshSnapshot();
    });
}

void WorkbenchController::probeDisconnect() {
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("disconnect"));
    m_engine->sendRequest(QStringLiteral("probe"), params, [this](bool, const QJsonObject &, const QString &) {
        refreshSnapshot();
    });
}

void WorkbenchController::probeHalt() {
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("halt"));
    m_engine->sendRequest(QStringLiteral("probe"), params, [this](bool, const QJsonObject &, const QString &) {
        refreshSnapshot();
    });
}

void WorkbenchController::probeResume() {
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("resume"));
    m_engine->sendRequest(QStringLiteral("probe"), params, [this](bool, const QJsonObject &, const QString &) {
        refreshSnapshot();
    });
}

void WorkbenchController::probeReset(bool haltOnReset) {
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("reset"));
    params.insert(QStringLiteral("halt_on_reset"), haltOnReset);
    m_engine->sendRequest(QStringLiteral("probe"), params, [this](bool, const QJsonObject &, const QString &) {
        refreshSnapshot();
    });
}

void WorkbenchController::rttStart(int channel) {
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("rtt_start"));
    params.insert(QStringLiteral("channel"), channel);
    m_engine->sendRequest(QStringLiteral("probe"), params, [this](bool, const QJsonObject &, const QString &) {
        refreshSnapshot();
    });
}

void WorkbenchController::rttStop() {
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("rtt_stop"));
    m_engine->sendRequest(QStringLiteral("probe"), params, [this](bool, const QJsonObject &, const QString &) {
        refreshSnapshot();
    });
}

void WorkbenchController::rttWrite(const QString &text) {
    if (!m_engine->isRunning() || text.isEmpty()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("rtt_write"));
    params.insert(QStringLiteral("command"), text);
    m_engine->sendRequest(QStringLiteral("probe"), params);
}

void WorkbenchController::uartStart(const QString &port, int baudrate) {
    if (!m_engine->isRunning()) {
        return;
    }
    if (m_settings) {
        if (!port.trimmed().isEmpty()) {
            m_settings->setUartPort(port.trimmed());
        }
        m_settings->setUartBaudrate(baudrate);
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("uart_start"));
    if (!port.trimmed().isEmpty()) {
        params.insert(QStringLiteral("port"), port.trimmed());
    }
    params.insert(QStringLiteral("baudrate"), baudrate);
    m_engine->sendRequest(QStringLiteral("probe"), params, [this](bool, const QJsonObject &, const QString &) {
        refreshSnapshot();
    });
}

void WorkbenchController::uartStop() {
    if (!m_engine->isRunning()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("uart_stop"));
    m_engine->sendRequest(QStringLiteral("probe"), params, [this](bool, const QJsonObject &, const QString &) {
        refreshSnapshot();
    });
}

void WorkbenchController::uartWrite(const QString &text) {
    if (!m_engine->isRunning() || text.isEmpty()) {
        return;
    }
    QJsonObject params;
    params.insert(QStringLiteral("action"), QStringLiteral("uart_write"));
    params.insert(QStringLiteral("command"), text);
    m_engine->sendRequest(QStringLiteral("probe"), params);
}

void WorkbenchController::clearRttLogs() {
    m_rttLogs->clear();
}

void WorkbenchController::clearUartLogs() {
    m_uartLogs->clear();
}

void WorkbenchController::clearEngineLogs() {
    m_engineLogs->clear();
}

void WorkbenchController::applyModelConfig(const QString &provider,
                                           const QString &model,
                                           const QString &baseUrl,
                                           const QString &apiKey,
                                           bool saveUserConfig) {
    const QString cleanProv = provider.trimmed().isEmpty() ? QStringLiteral("openai") : provider.trimmed();
    const QString cleanModel = model.trimmed();
    const QString cleanUrl = baseUrl.trimmed();
    const QString cleanKey = apiKey.trimmed();

    if (m_settings) {
        m_settings->setProvider(cleanProv);
        m_settings->setModel(cleanModel);
        m_settings->setBaseUrl(cleanUrl);
        m_settings->setRememberKey(saveUserConfig);
        if (saveUserConfig) {
            m_settings->setApiKey(cleanKey);
        }
        m_settings->setMockLlm(cleanProv == QStringLiteral("mock"));
    }

    m_provider = cleanProv;
    m_model = cleanModel;
    emit configChanged();

    if (m_engine->isRunning()) {
        QJsonObject params;
        params.insert(QStringLiteral("provider"), cleanProv);
        if (!cleanModel.isEmpty()) {
            params.insert(QStringLiteral("model"), cleanModel);
        }
        if (!cleanUrl.isEmpty()) {
            params.insert(QStringLiteral("base_url"), cleanUrl);
        }
        if (!cleanKey.isEmpty()) {
            params.insert(QStringLiteral("api_key"), cleanKey);
        }
        params.insert(QStringLiteral("mock_llm"), cleanProv == QStringLiteral("mock"));
        params.insert(QStringLiteral("save_user_config"), saveUserConfig);
        m_engine->sendRequest(QStringLiteral("configure"), params, [this](bool ok, const QJsonObject &res, const QString &) {
            if (ok) {
                m_provider = res.value(QStringLiteral("provider")).toString(m_provider);
                m_model = res.value(QStringLiteral("model")).toString(m_model);
                emit configChanged();
            }
        });
    }
}

void WorkbenchController::testModelConnection(const QString &provider,
                                              const QString &model,
                                              const QString &baseUrl,
                                              const QString &apiKey) {
    if (!m_settings) {
        return;
    }
    m_settings->setTestingConnection(true);
    if (!m_engine->isRunning()) {
        m_settings->setConnectionTestResult(false, tr("Engine is offline."));
        return;
    }

    QJsonObject params;
    params.insert(QStringLiteral("provider"), provider.trimmed());
    if (!model.trimmed().isEmpty()) {
        params.insert(QStringLiteral("model"), model.trimmed());
    }
    if (!baseUrl.trimmed().isEmpty()) {
        params.insert(QStringLiteral("base_url"), baseUrl.trimmed());
    }
    if (!apiKey.trimmed().isEmpty()) {
        params.insert(QStringLiteral("api_key"), apiKey.trimmed());
    }

    m_engine->sendRequest(
        QStringLiteral("test_connection"),
        params,
        [this](bool ok, const QJsonObject &res, const QString &err) {
            if (!m_settings) {
                return;
            }
            if (!ok) {
                m_settings->setConnectionTestResult(false, err);
                return;
            }
            const bool connOk = res.value(QStringLiteral("ok")).toBool(false);
            const int latency = res.value(QStringLiteral("latency_ms")).toInt(0);
            if (connOk) {
                m_settings->setConnectionTestResult(true, tr("Connected (%1 ms)").arg(latency));
            } else {
                m_settings->setConnectionTestResult(
                    false, res.value(QStringLiteral("error")).toString(tr("Connection failed")));
            }
        },
        15000);
}

void WorkbenchController::seedDemoState(const QString &state) {
    m_demoSeeded = true;
    DemoSeeder::seed(this, m_updater, m_diagnostics, state);
}
