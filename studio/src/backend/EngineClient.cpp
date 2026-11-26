#include "backend/EngineClient.h"
#include "BuildConfig.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QProcessEnvironment>
#include <QStandardPaths>
#include <QTimer>

namespace {

constexpr int kMaxStderrRingLines = 500;
constexpr int kMaxRestartAttempts = 3;
constexpr int kBackoffDelaysMs[3] = {1000, 2000, 5000};

bool isValidRepoRootDir(const QDir &dir) {
    return QFileInfo::exists(dir.filePath(QStringLiteral("src/mcu_harness/__init__.py")));
}

QString walkUpForRepoRoot(const QString &startPath) {
    if (startPath.isEmpty()) {
        return QString();
    }
    QFileInfo fi(startPath);
    QDir dir = fi.isFile() ? fi.dir() : QDir(startPath);
    for (int depth = 0; depth < 12; ++depth) {
        if (isValidRepoRootDir(dir)) {
            return dir.absolutePath();
        }
        if (!dir.cdUp()) {
            break;
        }
    }
    return QString();
}

} // namespace

EngineClient::EngineClient(QObject *parent)
    : QObject(parent)
    , m_restartTimer(new QTimer(this))
    , m_healthResetTimer(new QTimer(this)) {
    m_restartTimer->setSingleShot(true);
    connect(m_restartTimer, &QTimer::timeout, this, &EngineClient::spawnProcess);

    m_healthResetTimer->setSingleShot(true);
    m_healthResetTimer->setInterval(15000);
    connect(m_healthResetTimer, &QTimer::timeout, this, [this]() {
        m_restartAttempts = 0;
    });
}

EngineClient::~EngineClient() {
    shutdown();
}

QString EngineClient::discoverRepoRoot(const QString &explicitRoot) {
    if (!explicitRoot.trimmed().isEmpty()) {
        const QString found = walkUpForRepoRoot(explicitRoot.trimmed());
        if (!found.isEmpty()) {
            return found;
        }
    }

    const QString envRoot = qEnvironmentVariable("MCU_HARNESS_REPO").trimmed();
    if (!envRoot.isEmpty()) {
        const QString found = walkUpForRepoRoot(envRoot);
        if (!found.isEmpty()) {
            return found;
        }
    }

    if (QCoreApplication::instance()) {
        const QString found = walkUpForRepoRoot(QCoreApplication::applicationDirPath());
        if (!found.isEmpty()) {
            return found;
        }
    }

    const QString cwdFound = walkUpForRepoRoot(QDir::currentPath());
    if (!cwdFound.isEmpty()) {
        return cwdFound;
    }

    const QString compiledRoot = QStringLiteral(MCU_STUDIO_REPO_ROOT);
    if (isValidRepoRootDir(QDir(compiledRoot))) {
        return QDir(compiledRoot).absolutePath();
    }

    return QString();
}

QString EngineClient::discoverPythonBinary(const QString &repoRoot) {
    const QString envPy = qEnvironmentVariable("MCU_HARNESS_PYTHON").trimmed();
    if (!envPy.isEmpty()) {
        if (QFileInfo::exists(envPy)) {
            return envPy;
        }
        const QString foundEnv = QStandardPaths::findExecutable(envPy);
        if (!foundEnv.isEmpty()) {
            return foundEnv;
        }
    }

    if (!repoRoot.isEmpty()) {
        const QStringList venvCandidates = {
            QDir(repoRoot).filePath(QStringLiteral(".venv/bin/python")),
            QDir(repoRoot).filePath(QStringLiteral(".venv/bin/python3")),
            QDir(repoRoot).filePath(QStringLiteral(".venv/Scripts/python.exe")),
        };
        for (const QString &cand : venvCandidates) {
            if (QFileInfo::exists(cand) && QFileInfo(cand).isExecutable()) {
                return cand;
            }
        }
    }

#ifdef Q_OS_WIN
    const QString found = QStandardPaths::findExecutable(QStringLiteral("python"));
    if (!found.isEmpty()) {
        return found;
    }
    return QStringLiteral("python");
#else
    const QString found3 = QStandardPaths::findExecutable(QStringLiteral("python3"));
    if (!found3.isEmpty()) {
        return found3;
    }
    const QString foundPy = QStandardPaths::findExecutable(QStringLiteral("python"));
    return foundPy.isEmpty() ? QStringLiteral("python3") : foundPy;
#endif
}

bool EngineClient::parseNdjsonLine(const QByteArray &line, ParsedFrame *out, QString *outError) {
    const QByteArray trimmed = line.trimmed();
    if (trimmed.isEmpty()) {
        if (outError) {
            *outError = QStringLiteral("Empty NDJSON line");
        }
        return false;
    }

    QJsonParseError err{};
    const QJsonDocument doc = QJsonDocument::fromJson(trimmed, &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject()) {
        if (outError) {
            *outError = QStringLiteral("Invalid JSON frame: %1").arg(err.errorString());
        }
        return false;
    }

    const QJsonObject obj = doc.object();
    if (out) {
        *out = ParsedFrame{};
    }

    const QJsonValue idVal = obj.value(QStringLiteral("id"));
    if (obj.contains(QStringLiteral("id")) && !idVal.isNull() && !idVal.isUndefined()) {
        if (out) {
            out->type = FrameType::Response;
            out->id = idVal.toInteger(-1);
            out->ok = obj.value(QStringLiteral("ok")).toBool(false);
            out->result = obj.value(QStringLiteral("result")).toObject();
            out->error = obj.value(QStringLiteral("error")).toString();
        }
        return true;
    }

    QString kind = obj.value(QStringLiteral("event")).toString();
    if (kind.isEmpty()) {
        kind = obj.value(QStringLiteral("kind")).toString();
    }
    if (!kind.isEmpty()) {
        if (out) {
            out->type = FrameType::Event;
            out->eventKind = kind;
            out->seq = static_cast<quint64>(obj.value(QStringLiteral("seq")).toInteger(0));
            out->timestamp = obj.value(QStringLiteral("timestamp")).toDouble(0.0);
            out->data = obj.value(QStringLiteral("data")).toObject();
        }
        return true;
    }

    if (outError) {
        *outError = QStringLiteral("NDJSON frame missing both 'id' and 'event'/'kind'");
    }
    return false;
}

void EngineClient::start(const LaunchConfig &config) {
    shutdown();
    m_config = config;
    m_shuttingDown = false;
    m_restartAttempts = 0;
    spawnProcess();
}

void EngineClient::restart() {
    const LaunchConfig saved = m_config;
    start(saved);
}

void EngineClient::spawnProcess() {
    if (m_process) {
        m_process->disconnect(this);
        if (m_process->state() != QProcess::NotRunning) {
            m_process->kill();
            m_process->waitForFinished(500);
        }
        m_process->deleteLater();
        m_process = nullptr;
    }

    m_stdoutBuffer.clear();
    m_stderrBuffer.clear();

    const QString repoRoot = discoverRepoRoot(m_config.repoRoot);
    const QString pythonBin = m_config.pythonBin.trimmed().isEmpty()
                                  ? discoverPythonBinary(repoRoot)
                                  : m_config.pythonBin.trimmed();

    QString projDir = m_config.projectDir.trimmed();
    if (projDir.isEmpty()) {
        projDir = QDir::currentPath();
    }

    QStringList args;
    args << QStringLiteral("-u")
         << QStringLiteral("-m")
         << QStringLiteral("mcu_harness.agent.rpc")
         << projDir;

    if (!m_config.provider.trimmed().isEmpty()) {
        args << QStringLiteral("--provider") << m_config.provider.trimmed();
    }
    if (!m_config.model.trimmed().isEmpty()) {
        args << QStringLiteral("--model") << m_config.model.trimmed();
    }
    if (!m_config.baseUrl.trimmed().isEmpty()) {
        args << QStringLiteral("--base-url") << m_config.baseUrl.trimmed();
    }
    if (!m_config.apiKey.trimmed().isEmpty()) {
        args << QStringLiteral("--api-key") << m_config.apiKey.trimmed();
    }
    if (!m_config.mode.trimmed().isEmpty()) {
        args << QStringLiteral("--mode") << m_config.mode.trimmed();
    }
    if (m_config.mockLlm) {
        args << QStringLiteral("--mock-llm");
    }

    m_process = new QProcess(this);
    m_process->setProcessChannelMode(QProcess::SeparateChannels);

    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    env.insert(QStringLiteral("PYTHONUNBUFFERED"), QStringLiteral("1"));
    env.insert(QStringLiteral("PYTHONIOENCODING"), QStringLiteral("utf-8"));
    env.insert(QStringLiteral("PYTHONWARNINGS"), QStringLiteral("ignore::RuntimeWarning"));

    if (!repoRoot.isEmpty()) {
        const QString srcPath = QDir(repoRoot).filePath(QStringLiteral("src"));
        const QString existing = env.value(QStringLiteral("PYTHONPATH"));
        if (existing.isEmpty()) {
            env.insert(QStringLiteral("PYTHONPATH"), srcPath);
        } else {
            env.insert(QStringLiteral("PYTHONPATH"), srcPath + QDir::listSeparator() + existing);
        }
    }
    m_process->setProcessEnvironment(env);

    connect(m_process, &QProcess::readyReadStandardOutput, this, &EngineClient::onReadyReadStdout);
    connect(m_process, &QProcess::readyReadStandardError, this, &EngineClient::onReadyReadStderr);
    connect(m_process, &QProcess::finished, this, &EngineClient::onProcessFinished);
    connect(m_process, &QProcess::errorOccurred, this, &EngineClient::onProcessError);

    m_process->start(pythonBin, args, QIODevice::ReadWrite);
    if (!m_process->waitForStarted(3000)) {
        const QString err = tr("Failed to start Python RPC engine (%1): %2")
                                .arg(pythonBin, m_process->errorString());
        emit errorOccurred(err);
        scheduleRestart();
        return;
    }

    m_healthResetTimer->start();
    emit started();
}

void EngineClient::shutdown() {
    m_shuttingDown = true;
    m_restartTimer->stop();
    m_healthResetTimer->stop();

    if (m_process) {
        if (m_process->state() != QProcess::NotRunning) {
            const qint64 reqId = m_nextId++;
            QJsonObject req;
            req.insert(QStringLiteral("id"), reqId);
            req.insert(QStringLiteral("method"), QStringLiteral("shutdown"));
            req.insert(QStringLiteral("params"), QJsonObject());
            const QByteArray payload = QJsonDocument(req).toJson(QJsonDocument::Compact) + '\n';
            m_process->write(payload);
            m_process->waitForBytesWritten(300);
            m_process->closeWriteChannel();

            if (!m_process->waitForFinished(1500)) {
                m_process->terminate();
                if (!m_process->waitForFinished(1000)) {
                    m_process->kill();
                    m_process->waitForFinished(500);
                }
            }
        }
        m_process->disconnect(this);
        m_process->deleteLater();
        m_process = nullptr;
    }

    failAllPending(QStringLiteral("Engine shut down"));
}

bool EngineClient::isRunning() const {
    return m_process != nullptr && m_process->state() == QProcess::Running;
}

qint64 EngineClient::sendRequest(const QString &method,
                                 const QJsonObject &params,
                                 ResponseCallback cb,
                                 int timeoutMs) {
    if (!isRunning()) {
        if (cb) {
            cb(false, QJsonObject(), tr("Engine process is not running"));
        }
        return -1;
    }

    const qint64 id = m_nextId++;
    QJsonObject req;
    req.insert(QStringLiteral("id"), id);
    req.insert(QStringLiteral("method"), method);
    req.insert(QStringLiteral("params"), params);

    const QByteArray line = QJsonDocument(req).toJson(QJsonDocument::Compact) + '\n';

    PendingRequest pending;
    pending.method = method;
    pending.callback = std::move(cb);
    pending.timer = new QTimer(this);
    pending.timer->setSingleShot(true);
    pending.timer->setInterval(qMax(500, timeoutMs));
    connect(pending.timer, &QTimer::timeout, this, [this, id]() {
        auto it = m_pending.find(id);
        if (it == m_pending.end()) {
            return;
        }
        PendingRequest pr = std::move(it.value());
        m_pending.erase(it);
        if (pr.timer) {
            pr.timer->deleteLater();
        }
        if (pr.callback) {
            pr.callback(false, QJsonObject(), tr("RPC request '%1' timed out").arg(pr.method));
        }
    });
    m_pending.insert(id, std::move(pending));
    m_pending[id].timer->start();

    m_process->write(line);
    return id;
}

void EngineClient::onReadyReadStdout() {
    if (!m_process) {
        return;
    }
    m_stdoutBuffer.append(m_process->readAllStandardOutput());

    while (true) {
        const int nl = m_stdoutBuffer.indexOf('\n');
        if (nl < 0) {
            break;
        }
        QByteArray line = m_stdoutBuffer.left(nl);
        m_stdoutBuffer.remove(0, nl + 1);
        if (line.endsWith('\r')) {
            line.chop(1);
        }
        if (line.trimmed().isEmpty()) {
            continue;
        }

        ParsedFrame frame;
        QString parseErr;
        if (!parseNdjsonLine(line, &frame, &parseErr)) {
            appendStderrLine(QStringLiteral("[rpc-parse] %1").arg(parseErr));
            continue;
        }

        if (frame.type == FrameType::Response) {
            auto it = m_pending.find(frame.id);
            if (it != m_pending.end()) {
                PendingRequest pr = std::move(it.value());
                m_pending.erase(it);
                if (pr.timer) {
                    pr.timer->stop();
                    pr.timer->deleteLater();
                }
                if (pr.callback) {
                    pr.callback(frame.ok, frame.result, frame.error);
                }
            }
        } else if (frame.type == FrameType::Event) {
            if (frame.eventKind == QStringLiteral("ready")) {
                m_restartAttempts = 0;
                emit readyReceived(frame.data);
            }
            emit eventReceived(frame.eventKind, frame.seq, frame.timestamp, frame.data);
        }
    }
}

void EngineClient::onReadyReadStderr() {
    if (!m_process) {
        return;
    }
    m_stderrBuffer.append(m_process->readAllStandardError());

    while (true) {
        const int nl = m_stderrBuffer.indexOf('\n');
        if (nl < 0) {
            break;
        }
        QByteArray line = m_stderrBuffer.left(nl);
        m_stderrBuffer.remove(0, nl + 1);
        if (line.endsWith('\r')) {
            line.chop(1);
        }
        const QString text = QString::fromUtf8(line);
        if (!text.isEmpty()) {
            appendStderrLine(text);
        }
    }
}

void EngineClient::appendStderrLine(const QString &line) {
    m_stderrRing.append(line);
    while (m_stderrRing.size() > kMaxStderrRingLines) {
        m_stderrRing.removeFirst();
    }
    emit stderrLine(line);
}

void EngineClient::onProcessFinished(int exitCode, QProcess::ExitStatus exitStatus) {
    m_healthResetTimer->stop();
    if (!m_stderrBuffer.trimmed().isEmpty()) {
        appendStderrLine(QString::fromUtf8(m_stderrBuffer.trimmed()));
        m_stderrBuffer.clear();
    }

    if (m_shuttingDown) {
        return;
    }

    const QString reason = (exitStatus == QProcess::CrashExit)
                               ? tr("Engine process crashed (exit code %1)").arg(exitCode)
                               : tr("Engine process exited unexpectedly (code %1)").arg(exitCode);
    failAllPending(reason);
    emit exited(exitCode, reason);
    scheduleRestart();
}

void EngineClient::onProcessError(QProcess::ProcessError err) {
    if (m_shuttingDown) {
        return;
    }
    Q_UNUSED(err);
    const QString msg = m_process ? m_process->errorString() : tr("Unknown process error");
    appendStderrLine(QStringLiteral("[engine-error] %1").arg(msg));
    emit errorOccurred(msg);
}

void EngineClient::failAllPending(const QString &reason) {
    auto pendingCopy = std::move(m_pending);
    m_pending.clear();
    for (auto it = pendingCopy.begin(); it != pendingCopy.end(); ++it) {
        if (it.value().timer) {
            it.value().timer->stop();
            it.value().timer->deleteLater();
        }
        if (it.value().callback) {
            it.value().callback(false, QJsonObject(), reason);
        }
    }
}

void EngineClient::scheduleRestart() {
    if (!m_autoRestart || m_shuttingDown) {
        return;
    }
    if (m_restartAttempts >= kMaxRestartAttempts) {
        emit errorOccurred(tr("Engine failed to stay running after %1 restart attempts.").arg(kMaxRestartAttempts));
        return;
    }
    const int delay = kBackoffDelaysMs[qBound(0, m_restartAttempts, 2)];
    ++m_restartAttempts;
    m_restartTimer->start(delay);
}
