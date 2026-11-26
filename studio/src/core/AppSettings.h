#pragma once

#include <QObject>
#include <QSettings>
#include <QString>
#include <QStringList>

class AppSettings : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString theme READ theme WRITE setTheme NOTIFY themeChanged)
    Q_PROPERTY(QString effectiveTheme READ effectiveTheme NOTIFY themeChanged)
    Q_PROPERTY(QString language READ language WRITE setLanguage NOTIFY languageChanged)
    Q_PROPERTY(QString projectDir READ projectDir WRITE setProjectDir NOTIFY projectDirChanged)
    Q_PROPERTY(QString provider READ provider WRITE setProvider NOTIFY providerChanged)
    Q_PROPERTY(QString model READ model WRITE setModel NOTIFY modelChanged)
    Q_PROPERTY(QString baseUrl READ baseUrl WRITE setBaseUrl NOTIFY baseUrlChanged)
    Q_PROPERTY(QString apiKey READ apiKey WRITE setApiKey NOTIFY apiKeyChanged)
    Q_PROPERTY(QString interlockMode READ interlockMode WRITE setInterlockMode NOTIFY interlockModeChanged)
    Q_PROPERTY(bool mockLlm READ mockLlm WRITE setMockLlm NOTIFY mockLlmChanged)
    Q_PROPERTY(bool autoStartEngine READ autoStartEngine WRITE setAutoStartEngine NOTIFY autoStartEngineChanged)
    Q_PROPERTY(int telemetryIntervalMs READ telemetryIntervalMs WRITE setTelemetryIntervalMs NOTIFY telemetryIntervalMsChanged)
    Q_PROPERTY(int fontSize READ fontSize WRITE setFontSize NOTIFY fontSizeChanged)
    Q_PROPERTY(int editorFontSize READ editorFontSize WRITE setEditorFontSize NOTIFY editorFontSizeChanged)
    Q_PROPERTY(bool sidebarVisible READ sidebarVisible WRITE setSidebarVisible NOTIFY sidebarVisibleChanged)
    Q_PROPERTY(bool bottomPanelVisible READ bottomPanelVisible WRITE setBottomPanelVisible NOTIFY bottomPanelVisibleChanged)
    Q_PROPERTY(bool rightPanelVisible READ rightPanelVisible WRITE setRightPanelVisible NOTIFY rightPanelVisibleChanged)
    Q_PROPERTY(int sidebarWidth READ sidebarWidth WRITE setSidebarWidth NOTIFY sidebarWidthChanged)
    Q_PROPERTY(int bottomPanelHeight READ bottomPanelHeight WRITE setBottomPanelHeight NOTIFY bottomPanelHeightChanged)
    Q_PROPERTY(bool onboardingCompleted READ onboardingCompleted WRITE setOnboardingCompleted NOTIFY onboardingCompletedChanged)
    Q_PROPERTY(bool firstRunCompleted READ onboardingCompleted WRITE setOnboardingCompleted NOTIFY onboardingCompletedChanged)
    Q_PROPERTY(QString probeBackend READ probeBackend WRITE setProbeBackend NOTIFY probeBackendChanged)
    Q_PROPERTY(QString uartPort READ uartPort WRITE setUartPort NOTIFY uartPortChanged)
    Q_PROPERTY(int uartBaudrate READ uartBaudrate WRITE setUartBaudrate NOTIFY uartBaudrateChanged)
    Q_PROPERTY(bool autoCheckUpdates READ autoCheckUpdates WRITE setAutoCheckUpdates NOTIFY autoCheckUpdatesChanged)
    Q_PROPERTY(QString updateChannel READ updateChannel WRITE setUpdateChannel NOTIFY updateChannelChanged)
    Q_PROPERTY(QString updateUrl READ updateUrl WRITE setUpdateUrl NOTIFY updateUrlChanged)
    Q_PROPERTY(bool crashUploadConsent READ crashUploadConsent WRITE setCrashUploadConsent NOTIFY crashUploadConsentChanged)
    Q_PROPERTY(bool telemetryOptIn READ crashUploadConsent WRITE setCrashUploadConsent NOTIFY crashUploadConsentChanged)
    Q_PROPERTY(bool rememberKey READ rememberKey WRITE setRememberKey NOTIFY rememberKeyChanged)
    Q_PROPERTY(bool autoRefresh READ autoRefresh WRITE setAutoRefresh NOTIFY autoRefreshChanged)
    Q_PROPERTY(bool testingConnection READ testingConnection NOTIFY connectionTestChanged)
    Q_PROPERTY(QString connectionTestResult READ connectionTestResult NOTIFY connectionTestChanged)
    Q_PROPERTY(bool connectionTestOk READ connectionTestOk NOTIFY connectionTestChanged)
    Q_PROPERTY(QStringList recentProjects READ recentProjects NOTIFY recentProjectsChanged)
    Q_PROPERTY(QStringList watchExpressions READ watchExpressions WRITE setWatchExpressions NOTIFY watchExpressionsChanged)

public:
    explicit AppSettings(bool ephemeral = false, QObject *parent = nullptr);

    QString theme() const { return m_theme; }
    Q_INVOKABLE void setTheme(const QString &value);
    QString effectiveTheme() const;

    QString language() const { return m_language; }
    Q_INVOKABLE void setLanguage(const QString &value);

    QString projectDir() const { return m_projectDir; }
    void setProjectDir(const QString &value);

    QString provider() const { return m_provider; }
    void setProvider(const QString &value);

    QString model() const { return m_model; }
    void setModel(const QString &value);

    QString baseUrl() const { return m_baseUrl; }
    void setBaseUrl(const QString &value);

    QString apiKey() const { return m_apiKey; }
    void setApiKey(const QString &value);

    QString interlockMode() const { return m_interlockMode; }
    void setInterlockMode(const QString &value);

    bool mockLlm() const { return m_mockLlm; }
    void setMockLlm(bool value);

    bool autoStartEngine() const { return m_autoStartEngine; }
    void setAutoStartEngine(bool value);

    int telemetryIntervalMs() const { return m_telemetryIntervalMs; }
    void setTelemetryIntervalMs(int value);

    int fontSize() const { return m_fontSize; }
    void setFontSize(int value);

    int editorFontSize() const { return m_editorFontSize; }
    void setEditorFontSize(int value);

    bool sidebarVisible() const { return m_sidebarVisible; }
    void setSidebarVisible(bool value);

    bool bottomPanelVisible() const { return m_bottomPanelVisible; }
    void setBottomPanelVisible(bool value);

    bool rightPanelVisible() const { return m_rightPanelVisible; }
    void setRightPanelVisible(bool value);

    int sidebarWidth() const { return m_sidebarWidth; }
    void setSidebarWidth(int value);

    int bottomPanelHeight() const { return m_bottomPanelHeight; }
    void setBottomPanelHeight(int value);

    bool onboardingCompleted() const { return m_onboardingCompleted; }
    Q_INVOKABLE void setOnboardingCompleted(bool value);
    Q_INVOKABLE void setFirstRunCompleted(bool value) { setOnboardingCompleted(value); }

    QString probeBackend() const { return m_probeBackend; }
    Q_INVOKABLE void setProbeBackend(const QString &value);

    QString uartPort() const { return m_uartPort; }
    Q_INVOKABLE void setUartPort(const QString &value);

    int uartBaudrate() const { return m_uartBaudrate; }
    Q_INVOKABLE void setUartBaudrate(int value);

    bool autoCheckUpdates() const { return m_autoCheckUpdates; }
    void setAutoCheckUpdates(bool value);

    QString updateChannel() const { return m_updateChannel; }
    void setUpdateChannel(const QString &value);

    QString updateUrl() const { return m_updateUrl; }
    void setUpdateUrl(const QString &value);

    bool crashUploadConsent() const { return m_crashUploadConsent; }
    void setCrashUploadConsent(bool value);

    bool rememberKey() const { return m_rememberKey; }
    void setRememberKey(bool value);

    bool autoRefresh() const { return m_autoRefresh; }
    void setAutoRefresh(bool value);

    bool testingConnection() const { return m_testingConnection; }
    QString connectionTestResult() const { return m_connectionTestResult; }
    bool connectionTestOk() const { return m_connectionTestOk; }
    void setTestingConnection(bool testing);
    void setConnectionTestResult(bool ok, const QString &message);

    QStringList recentProjects() const { return m_recentProjects; }
    QStringList watchExpressions() const { return m_watchExpressions; }
    void setWatchExpressions(const QStringList &value);

    Q_INVOKABLE void addRecentProject(const QString &path);
    Q_INVOKABLE void clearRecentProjects();
    Q_INVOKABLE void resetToDefaults();
    Q_INVOKABLE void sync();

    void setEphemeral(bool ephemeral) { m_ephemeral = ephemeral; }

signals:
    void themeChanged();
    void languageChanged();
    void projectDirChanged();
    void providerChanged();
    void modelChanged();
    void baseUrlChanged();
    void apiKeyChanged();
    void interlockModeChanged();
    void mockLlmChanged();
    void autoStartEngineChanged();
    void telemetryIntervalMsChanged();
    void fontSizeChanged();
    void editorFontSizeChanged();
    void sidebarVisibleChanged();
    void bottomPanelVisibleChanged();
    void rightPanelVisibleChanged();
    void sidebarWidthChanged();
    void bottomPanelHeightChanged();
    void onboardingCompletedChanged();
    void probeBackendChanged();
    void uartPortChanged();
    void uartBaudrateChanged();
    void autoCheckUpdatesChanged();
    void updateChannelChanged();
    void updateUrlChanged();
    void crashUploadConsentChanged();
    void rememberKeyChanged();
    void autoRefreshChanged();
    void connectionTestChanged();
    void recentProjectsChanged();
    void watchExpressionsChanged();

private:
    void loadFromStorage();
    void saveKey(const QString &key, const QVariant &value);

    bool m_ephemeral = false;
    QSettings m_settings;

    QString m_theme;
    QString m_language;
    QString m_projectDir;
    QString m_provider;
    QString m_model;
    QString m_baseUrl;
    QString m_apiKey;
    QString m_interlockMode;
    bool m_mockLlm = false;
    bool m_autoStartEngine = true;
    int m_telemetryIntervalMs = 1000;
    int m_fontSize = 13;
    int m_editorFontSize = 12;
    bool m_sidebarVisible = true;
    bool m_bottomPanelVisible = false;
    bool m_rightPanelVisible = true;
    int m_sidebarWidth = 260;
    int m_bottomPanelHeight = 220;
    bool m_onboardingCompleted = false;
    QString m_probeBackend;
    QString m_uartPort;
    int m_uartBaudrate = 115200;
    bool m_autoCheckUpdates = true;
    QString m_updateChannel;
    QString m_updateUrl;
    bool m_crashUploadConsent = false;
    bool m_rememberKey = true;
    bool m_autoRefresh = true;
    bool m_testingConnection = false;
    QString m_connectionTestResult;
    bool m_connectionTestOk = false;
    QStringList m_recentProjects;
    QStringList m_watchExpressions;
};
