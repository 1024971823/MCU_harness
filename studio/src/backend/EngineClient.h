#pragma once

#include <QByteArray>
#include <QHash>
#include <QJsonObject>
#include <QObject>
#include <QProcess>
#include <QString>
#include <QStringList>

#include <functional>

class QTimer;

class EngineClient : public QObject {
    Q_OBJECT

public:
    struct LaunchConfig {
        QString pythonBin;
        QString repoRoot;
        QString projectDir;
        QString provider;
        QString model;
        QString baseUrl;
        QString apiKey;
        QString mode = QStringLiteral("interactive");
        bool mockLlm = false;
    };

    enum class FrameType {
        Invalid,
        Response,
        Event,
    };

    struct ParsedFrame {
        FrameType type = FrameType::Invalid;
        qint64 id = -1;
        bool ok = false;
        QJsonObject result;
        QString error;
        QString eventKind;
        quint64 seq = 0;
        double timestamp = 0.0;
        QJsonObject data;
    };

    using ResponseCallback = std::function<void(bool ok, const QJsonObject &result, const QString &error)>;

    explicit EngineClient(QObject *parent = nullptr);
    ~EngineClient() override;

    static QString discoverRepoRoot(const QString &explicitRoot = QString());
    static QString discoverPythonBinary(const QString &repoRoot = QString());
    static bool parseNdjsonLine(const QByteArray &line, ParsedFrame *out, QString *outError = nullptr);

    void start(const LaunchConfig &config);
    void restart();
    void shutdown();

    bool isRunning() const;
    LaunchConfig config() const { return m_config; }
    QStringList recentStderrLines() const { return m_stderrRing; }

    void setAutoRestart(bool enabled) { m_autoRestart = enabled; }
    bool autoRestart() const { return m_autoRestart; }

    qint64 sendRequest(const QString &method,
                       const QJsonObject &params = QJsonObject(),
                       ResponseCallback cb = nullptr,
                       int timeoutMs = 30000);

signals:
    void started();
    void readyReceived(const QJsonObject &data);
    void eventReceived(const QString &kind, quint64 seq, double timestamp, const QJsonObject &data);
    void stderrLine(const QString &line);
    void exited(int exitCode, const QString &reason);
    void errorOccurred(const QString &error);

private slots:
    void onReadyReadStdout();
    void onReadyReadStderr();
    void onProcessFinished(int exitCode, QProcess::ExitStatus exitStatus);
    void onProcessError(QProcess::ProcessError err);

private:
    struct PendingRequest {
        QString method;
        ResponseCallback callback;
        QTimer *timer = nullptr;
    };

    void spawnProcess();
    void failAllPending(const QString &reason);
    void scheduleRestart();
    void appendStderrLine(const QString &line);

    LaunchConfig m_config;
    QProcess *m_process = nullptr;
    QByteArray m_stdoutBuffer;
    QByteArray m_stderrBuffer;
    QStringList m_stderrRing;
    QHash<qint64, PendingRequest> m_pending;
    qint64 m_nextId = 1;
    bool m_shuttingDown = false;
    bool m_autoRestart = true;
    int m_restartAttempts = 0;
    QTimer *m_restartTimer = nullptr;
    QTimer *m_healthResetTimer = nullptr;
};
