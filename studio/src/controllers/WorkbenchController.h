#pragma once

#include <QJsonArray>
#include <QJsonObject>
#include <QList>
#include <QObject>
#include <QString>
#include <QStringList>
#include <QVariantMap>

class AppInfo;
class AppSettings;
class AuditModel;
class ConversationModel;
class DiagnosticsService;
class EngineClient;
class LogModel;
class PlaybookModel;
class QTimer;
class RtosHazardModel;
class RtosTaskModel;
class SessionModel;
class UpdateManager;
class WatchModel;

class WorkbenchController : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString engineStatus READ engineStatus NOTIFY engineStatusChanged)
    Q_PROPERTY(QString engineError READ engineError NOTIFY engineStatusChanged)
    Q_PROPERTY(bool turnRunning READ turnRunning NOTIFY turnRunningChanged)
    Q_PROPERTY(QString provider READ provider NOTIFY configChanged)
    Q_PROPERTY(QString model READ model NOTIFY configChanged)
    Q_PROPERTY(QString interlockMode READ interlockMode NOTIFY configChanged)
    Q_PROPERTY(QString sessionId READ sessionId NOTIFY sessionChanged)
    Q_PROPERTY(QString statusCard READ statusCard NOTIFY telemetryChanged)
    Q_PROPERTY(QString tokenUsageText READ tokenUsageText NOTIFY tokenUsageChanged)
    Q_PROPERTY(int pendingApprovalsCount READ pendingApprovalsCount NOTIFY pendingApprovalsChanged)
    Q_PROPERTY(QVariantMap currentApproval READ currentApproval NOTIFY pendingApprovalsChanged)
    Q_PROPERTY(QVariantMap target READ target NOTIFY telemetryChanged)
    Q_PROPERTY(QVariantMap probe READ probe NOTIFY telemetryChanged)
    Q_PROPERTY(QVariantMap rtt READ rtt NOTIFY telemetryChanged)
    Q_PROPERTY(QVariantMap uart READ uart NOTIFY telemetryChanged)
    Q_PROPERTY(QVariantMap rtos READ rtos NOTIFY telemetryChanged)

    Q_PROPERTY(QObject *messagesModel READ messagesModel CONSTANT)
    Q_PROPERTY(QObject *playbooksModel READ playbooksModel CONSTANT)
    Q_PROPERTY(QObject *sessionsModel READ sessionsModel CONSTANT)
    Q_PROPERTY(QObject *rttLogModel READ rttLogModel CONSTANT)
    Q_PROPERTY(QObject *uartLogModel READ uartLogModel CONSTANT)
    Q_PROPERTY(QObject *engineLogModel READ engineLogModel CONSTANT)
    Q_PROPERTY(QObject *watchModel READ watchModel CONSTANT)
    Q_PROPERTY(QObject *rtosTasksModel READ rtosTasksModel CONSTANT)
    Q_PROPERTY(QObject *rtosHazardsModel READ rtosHazardsModel CONSTANT)
    Q_PROPERTY(QObject *auditModel READ auditModel CONSTANT)

public:
    WorkbenchController(AppSettings *settings,
                        AppInfo *appInfo,
                        UpdateManager *updater,
                        DiagnosticsService *diagnostics,
                        bool demoMode = false,
                        QObject *parent = nullptr);
    ~WorkbenchController() override;

    void startEngine();
    void shutdownEngine();
    void setWindowMinimized(bool minimized);

    QString engineStatus() const { return m_engineStatus; }
    QString engineError() const { return m_engineError; }
    bool turnRunning() const { return m_turnRunning; }
    QString provider() const { return m_provider; }
    QString model() const { return m_model; }
    QString interlockMode() const { return m_interlockMode; }
    QString sessionId() const { return m_sessionId; }
    QString statusCard() const { return m_statusCard; }
    QString tokenUsageText() const { return m_tokenUsageText; }
    int pendingApprovalsCount() const { return m_pendingApprovals.size(); }
    QVariantMap currentApproval() const;
    QVariantMap target() const { return m_target; }
    QVariantMap probe() const { return m_probe; }
    QVariantMap rtt() const { return m_rtt; }
    QVariantMap uart() const { return m_uart; }
    QVariantMap rtos() const { return m_rtos; }

    QObject *messagesModel() const;
    QObject *playbooksModel() const;
    QObject *sessionsModel() const;
    QObject *rttLogModel() const;
    QObject *uartLogModel() const;
    QObject *engineLogModel() const;
    QObject *watchModel() const;
    QObject *rtosTasksModel() const;
    QObject *rtosHazardsModel() const;
    QObject *auditModel() const;

    ConversationModel *conversation() const { return m_conversation; }
    PlaybookModel *playbooks() const { return m_playbooks; }
    SessionModel *sessions() const { return m_sessions; }
    LogModel *rttLogs() const { return m_rttLogs; }
    LogModel *uartLogs() const { return m_uartLogs; }
    LogModel *engineLogs() const { return m_engineLogs; }
    WatchModel *watch() const { return m_watch; }
    RtosTaskModel *rtosTasks() const { return m_rtosTasks; }
    RtosHazardModel *rtosHazards() const { return m_rtosHazards; }
    AuditModel *audit() const { return m_audit; }
    EngineClient *engineClient() const { return m_engine; }

    void setTargetMap(const QVariantMap &m);
    void setProbeMap(const QVariantMap &m);
    void setRttMap(const QVariantMap &m);
    void setUartMap(const QVariantMap &m);
    void setRtosMap(const QVariantMap &m);
    void setStatusCardText(const QString &md);
    void setTokenUsageText(const QString &text);
    void setEngineReadyDemo(const QString &provider, const QString &model, const QString &mode, const QString &sessionId);

    Q_INVOKABLE void sendMessage(const QString &text);
    Q_INVOKABLE void abortTurn();
    Q_INVOKABLE void resolveApproval(const QString &requestId,
                                     bool approved,
                                     const QString &scope = QStringLiteral("once"),
                                     const QString &reason = QString());
    Q_INVOKABLE void setInterlockMode(const QString &mode);
    Q_INVOKABLE void openProject(const QString &pathOrUrl);
    Q_INVOKABLE void newSession();
    Q_INVOKABLE void loadSession(const QString &sessionId);
    Q_INVOKABLE void refreshSessions();
    Q_INVOKABLE void refreshSnapshot();
    Q_INVOKABLE void addWatch(const QString &expr);
    Q_INVOKABLE void removeWatch(const QString &expr);
    Q_INVOKABLE void clearWatch();
    Q_INVOKABLE void probeConnect(const QString &backend = QString());
    Q_INVOKABLE void probeDisconnect();
    Q_INVOKABLE void probeHalt();
    Q_INVOKABLE void probeResume();
    Q_INVOKABLE void probeReset(bool haltOnReset);
    Q_INVOKABLE void rttStart(int channel = 0);
    Q_INVOKABLE void rttStop();
    Q_INVOKABLE void rttWrite(const QString &text);
    Q_INVOKABLE void uartStart(const QString &port, int baudrate = 115200);
    Q_INVOKABLE void uartStop();
    Q_INVOKABLE void uartWrite(const QString &text);
    Q_INVOKABLE void clearRttLogs();
    Q_INVOKABLE void clearUartLogs();
    Q_INVOKABLE void clearEngineLogs();
    Q_INVOKABLE void restartEngine();
    Q_INVOKABLE void applyModelConfig(const QString &provider,
                                      const QString &model,
                                      const QString &baseUrl,
                                      const QString &apiKey,
                                      bool saveUserConfig = false);
    Q_INVOKABLE void testModelConnection(const QString &provider,
                                         const QString &model,
                                         const QString &baseUrl,
                                         const QString &apiKey);
    Q_INVOKABLE void seedDemoState(const QString &state);

signals:
    void engineStatusChanged();
    void turnRunningChanged();
    void configChanged();
    void sessionChanged();
    void telemetryChanged();
    void tokenUsageChanged();
    void pendingApprovalsChanged();

private slots:
    void onEngineStarted();
    void onEngineReady(const QJsonObject &data);
    void onEngineEvent(const QString &kind, quint64 seq, double timestamp, const QJsonObject &data);
    void onEngineStderr(const QString &line);
    void onEngineExited(int exitCode, const QString &reason);
    void onEngineError(const QString &error);
    void onPollTimerTick();

private:
    struct ApprovalItem {
        QString requestId;
        QString toolName;
        QString riskLevel;
        QString summary;
        QString detailsJson;
        QString diffPreview;
        QString svdPreview;
        bool isHardwareDanger = false;
    };

    void applyTelemetrySnapshot(const QJsonObject &snap);
    void pollLogsIncremental(const QString &source);
    void updatePollingRate();
    void enqueueApproval(const QJsonObject &reqObj);
    void removeApprovalById(const QString &reqId);
    void setEngineStatus(const QString &status, const QString &error = QString());

    AppSettings *m_settings = nullptr;
    AppInfo *m_appInfo = nullptr;
    UpdateManager *m_updater = nullptr;
    DiagnosticsService *m_diagnostics = nullptr;
    bool m_demoMode = false;
    bool m_demoSeeded = false;

    EngineClient *m_engine = nullptr;
    ConversationModel *m_conversation = nullptr;
    PlaybookModel *m_playbooks = nullptr;
    SessionModel *m_sessions = nullptr;
    LogModel *m_rttLogs = nullptr;
    LogModel *m_uartLogs = nullptr;
    LogModel *m_engineLogs = nullptr;
    WatchModel *m_watch = nullptr;
    RtosTaskModel *m_rtosTasks = nullptr;
    RtosHazardModel *m_rtosHazards = nullptr;
    AuditModel *m_audit = nullptr;

    QTimer *m_pollTimer = nullptr;
    bool m_telemetryInFlight = false;
    bool m_rttLogsInFlight = false;
    bool m_uartLogsInFlight = false;
    bool m_windowMinimized = false;

    QString m_engineStatus = QStringLiteral("stopped");
    QString m_engineError;
    bool m_turnRunning = false;
    QString m_provider;
    QString m_model;
    QString m_interlockMode = QStringLiteral("interactive");
    QString m_sessionId;
    QString m_statusCard;
    QString m_tokenUsageText;
    QStringList m_watchExprs;
    QList<ApprovalItem> m_pendingApprovals;

    QVariantMap m_target;
    QVariantMap m_probe;
    QVariantMap m_rtt;
    QVariantMap m_uart;
    QVariantMap m_rtos;
};
