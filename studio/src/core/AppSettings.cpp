#include "core/AppSettings.h"
#include "BuildConfig.h"

#include <QDir>
#include <QGuiApplication>
#include <QLocale>
#include <QStyleHints>

namespace {

QString defaultLanguageForLocale() {
    const QString loc = QLocale::system().name();
    return loc.startsWith(QStringLiteral("zh"), Qt::CaseInsensitive)
               ? QStringLiteral("zh_CN")
               : QStringLiteral("en");
}

} // namespace

AppSettings::AppSettings(bool ephemeral, QObject *parent)
    : QObject(parent)
    , m_ephemeral(ephemeral)
    , m_settings(QStringLiteral("Lannuo"), QStringLiteral("MCU Harness Studio")) {
    loadFromStorage();
}

void AppSettings::loadFromStorage() {
    m_theme = m_settings.value(QStringLiteral("appearance/theme"), QStringLiteral("dark")).toString();
    m_language = m_settings.value(QStringLiteral("appearance/language"), defaultLanguageForLocale()).toString();
    m_fontSize = qBound(10, m_settings.value(QStringLiteral("appearance/fontSize"), 13).toInt(), 20);
    m_editorFontSize = qBound(10, m_settings.value(QStringLiteral("appearance/editorFontSize"), 12).toInt(), 20);

    m_projectDir = m_settings.value(QStringLiteral("workspace/projectDir"), QString()).toString();
    m_recentProjects = m_settings.value(QStringLiteral("workspace/recentProjects")).toStringList();
    m_watchExpressions = m_settings.value(QStringLiteral("workspace/watchExpressions")).toStringList();

    m_provider = m_settings.value(QStringLiteral("agent/provider"), QStringLiteral("deepseek")).toString();
    m_model = m_settings.value(QStringLiteral("agent/model"), QString()).toString();
    m_baseUrl = m_settings.value(QStringLiteral("agent/baseUrl"), QString()).toString();
    m_apiKey.clear(); // API keys are never persisted to QSettings; stored in engine ~/.config/mcu-harness/agent.toml (0600) only
    m_interlockMode = m_settings.value(QStringLiteral("agent/interlockMode"), QStringLiteral("interactive")).toString();
    m_mockLlm = m_settings.value(QStringLiteral("agent/mockLlm"), false).toBool();

    m_autoStartEngine = m_settings.value(QStringLiteral("engine/autoStart"), true).toBool();
    m_telemetryIntervalMs = qBound(250, m_settings.value(QStringLiteral("engine/telemetryIntervalMs"), 1000).toInt(), 10000);

    m_sidebarVisible = m_settings.value(QStringLiteral("layout/sidebarVisible"), true).toBool();
    m_bottomPanelVisible = m_settings.value(QStringLiteral("layout/bottomPanelVisible"), false).toBool();
    m_rightPanelVisible = m_settings.value(QStringLiteral("layout/rightPanelVisible"), true).toBool();
    m_sidebarWidth = qBound(180, m_settings.value(QStringLiteral("layout/sidebarWidth"), 260).toInt(), 480);
    m_bottomPanelHeight = qBound(120, m_settings.value(QStringLiteral("layout/bottomPanelHeight"), 220).toInt(), 500);

    m_onboardingCompleted = m_settings.value(QStringLiteral("app/onboardingCompleted"), false).toBool();
    m_probeBackend = m_settings.value(QStringLiteral("hardware/probeBackend"), QStringLiteral("jlink")).toString();
    m_uartPort = m_settings.value(QStringLiteral("hardware/uartPort"), QString()).toString();
    m_uartBaudrate = m_settings.value(QStringLiteral("hardware/uartBaudrate"), 115200).toInt();
    m_autoCheckUpdates = m_settings.value(QStringLiteral("updates/autoCheck"), true).toBool();
    m_updateChannel = m_settings.value(QStringLiteral("updates/channel"), QStringLiteral("stable")).toString();
    m_updateUrl = m_settings.value(QStringLiteral("updates/url"), QStringLiteral(MCU_STUDIO_UPDATE_URL)).toString();
    m_crashUploadConsent = m_settings.value(QStringLiteral("diagnostics/crashUploadConsent"), false).toBool();
    m_rememberKey = m_settings.value(QStringLiteral("agent/rememberKey"), true).toBool();
    m_autoRefresh = m_settings.value(QStringLiteral("engine/autoRefresh"), true).toBool();
}

void AppSettings::saveKey(const QString &key, const QVariant &value) {
    if (!m_ephemeral) {
        m_settings.setValue(key, value);
    }
}

QString AppSettings::effectiveTheme() const {
    if (m_theme == QStringLiteral("light") || m_theme == QStringLiteral("dark")) {
        return m_theme;
    }
    if (QGuiApplication::styleHints()) {
        if (QGuiApplication::styleHints()->colorScheme() == Qt::ColorScheme::Light) {
            return QStringLiteral("light");
        }
    }
    return QStringLiteral("dark");
}

void AppSettings::setTheme(const QString &value) {
    const QString norm = (value == QStringLiteral("light") || value == QStringLiteral("system"))
                             ? value
                             : QStringLiteral("dark");
    if (m_theme != norm) {
        m_theme = norm;
        saveKey(QStringLiteral("appearance/theme"), norm);
        emit themeChanged();
    }
}

void AppSettings::setLanguage(const QString &value) {
    const QString norm = (value.startsWith(QStringLiteral("zh"), Qt::CaseInsensitive))
                             ? QStringLiteral("zh_CN")
                             : QStringLiteral("en");
    if (m_language != norm) {
        m_language = norm;
        saveKey(QStringLiteral("appearance/language"), norm);
        emit languageChanged();
    }
}

void AppSettings::setProjectDir(const QString &value) {
    const QString trimmed = value.trimmed();
    if (m_projectDir != trimmed) {
        m_projectDir = trimmed;
        saveKey(QStringLiteral("workspace/projectDir"), trimmed);
        emit projectDirChanged();
    }
}

void AppSettings::setProvider(const QString &value) {
    const QString norm = value.trimmed().isEmpty() ? QStringLiteral("deepseek") : value.trimmed();
    if (m_provider != norm) {
        m_provider = norm;
        saveKey(QStringLiteral("agent/provider"), norm);
        emit providerChanged();
    }
}

void AppSettings::setModel(const QString &value) {
    const QString trimmed = value.trimmed();
    if (m_model != trimmed) {
        m_model = trimmed;
        saveKey(QStringLiteral("agent/model"), trimmed);
        emit modelChanged();
    }
}

void AppSettings::setBaseUrl(const QString &value) {
    const QString trimmed = value.trimmed();
    if (m_baseUrl != trimmed) {
        m_baseUrl = trimmed;
        saveKey(QStringLiteral("agent/baseUrl"), trimmed);
        emit baseUrlChanged();
    }
}

void AppSettings::setApiKey(const QString &value) {
    const QString trimmed = value.trimmed();
    if (m_apiKey != trimmed) {
        m_apiKey = trimmed;
        emit apiKeyChanged();
    }
}

void AppSettings::setInterlockMode(const QString &value) {
    QString norm = value.trimmed().toLower();
    if (norm != QStringLiteral("interactive") && norm != QStringLiteral("auto_safe") && norm != QStringLiteral("read_only")) {
        norm = QStringLiteral("interactive");
    }
    if (m_interlockMode != norm) {
        m_interlockMode = norm;
        saveKey(QStringLiteral("agent/interlockMode"), norm);
        emit interlockModeChanged();
    }
}

void AppSettings::setMockLlm(bool value) {
    if (m_mockLlm != value) {
        m_mockLlm = value;
        saveKey(QStringLiteral("agent/mockLlm"), value);
        emit mockLlmChanged();
    }
}

void AppSettings::setAutoStartEngine(bool value) {
    if (m_autoStartEngine != value) {
        m_autoStartEngine = value;
        saveKey(QStringLiteral("engine/autoStart"), value);
        emit autoStartEngineChanged();
    }
}

void AppSettings::setTelemetryIntervalMs(int value) {
    const int clamped = qBound(250, value, 10000);
    if (m_telemetryIntervalMs != clamped) {
        m_telemetryIntervalMs = clamped;
        saveKey(QStringLiteral("engine/telemetryIntervalMs"), clamped);
        emit telemetryIntervalMsChanged();
    }
}

void AppSettings::setFontSize(int value) {
    const int clamped = qBound(10, value, 20);
    if (m_fontSize != clamped) {
        m_fontSize = clamped;
        saveKey(QStringLiteral("appearance/fontSize"), clamped);
        emit fontSizeChanged();
    }
}

void AppSettings::setEditorFontSize(int value) {
    const int clamped = qBound(10, value, 20);
    if (m_editorFontSize != clamped) {
        m_editorFontSize = clamped;
        saveKey(QStringLiteral("appearance/editorFontSize"), clamped);
        emit editorFontSizeChanged();
    }
}

void AppSettings::setSidebarVisible(bool value) {
    if (m_sidebarVisible != value) {
        m_sidebarVisible = value;
        saveKey(QStringLiteral("layout/sidebarVisible"), value);
        emit sidebarVisibleChanged();
    }
}

void AppSettings::setBottomPanelVisible(bool value) {
    if (m_bottomPanelVisible != value) {
        m_bottomPanelVisible = value;
        saveKey(QStringLiteral("layout/bottomPanelVisible"), value);
        emit bottomPanelVisibleChanged();
    }
}

void AppSettings::setRightPanelVisible(bool value) {
    if (m_rightPanelVisible != value) {
        m_rightPanelVisible = value;
        saveKey(QStringLiteral("layout/rightPanelVisible"), value);
        emit rightPanelVisibleChanged();
    }
}

void AppSettings::setSidebarWidth(int value) {
    const int clamped = qBound(180, value, 480);
    if (m_sidebarWidth != clamped) {
        m_sidebarWidth = clamped;
        saveKey(QStringLiteral("layout/sidebarWidth"), clamped);
        emit sidebarWidthChanged();
    }
}

void AppSettings::setBottomPanelHeight(int value) {
    const int clamped = qBound(120, value, 500);
    if (m_bottomPanelHeight != clamped) {
        m_bottomPanelHeight = clamped;
        saveKey(QStringLiteral("layout/bottomPanelHeight"), clamped);
        emit bottomPanelHeightChanged();
    }
}

void AppSettings::setOnboardingCompleted(bool value) {
    if (m_onboardingCompleted != value) {
        m_onboardingCompleted = value;
        saveKey(QStringLiteral("app/onboardingCompleted"), value);
        emit onboardingCompletedChanged();
    }
}

void AppSettings::setProbeBackend(const QString &value) {
    const QString norm = value.trimmed().isEmpty() ? QStringLiteral("jlink") : value.trimmed();
    if (m_probeBackend != norm) {
        m_probeBackend = norm;
        saveKey(QStringLiteral("hardware/probeBackend"), norm);
        emit probeBackendChanged();
    }
}

void AppSettings::setUartPort(const QString &value) {
    const QString trimmed = value.trimmed();
    if (m_uartPort != trimmed) {
        m_uartPort = trimmed;
        saveKey(QStringLiteral("hardware/uartPort"), trimmed);
        emit uartPortChanged();
    }
}

void AppSettings::setUartBaudrate(int value) {
    const int clamped = qBound(1200, value, 4000000);
    if (m_uartBaudrate != clamped) {
        m_uartBaudrate = clamped;
        saveKey(QStringLiteral("hardware/uartBaudrate"), clamped);
        emit uartBaudrateChanged();
    }
}

void AppSettings::setAutoCheckUpdates(bool value) {
    if (m_autoCheckUpdates != value) {
        m_autoCheckUpdates = value;
        saveKey(QStringLiteral("updates/autoCheck"), value);
        emit autoCheckUpdatesChanged();
    }
}

void AppSettings::setUpdateChannel(const QString &value) {
    const QString norm = value.trimmed().isEmpty() ? QStringLiteral("stable") : value.trimmed();
    if (m_updateChannel != norm) {
        m_updateChannel = norm;
        saveKey(QStringLiteral("updates/channel"), norm);
        emit updateChannelChanged();
    }
}

void AppSettings::setUpdateUrl(const QString &value) {
    const QString trimmed = value.trimmed();
    if (m_updateUrl != trimmed) {
        m_updateUrl = trimmed;
        saveKey(QStringLiteral("updates/url"), trimmed);
        emit updateUrlChanged();
    }
}

void AppSettings::setCrashUploadConsent(bool value) {
    if (m_crashUploadConsent != value) {
        m_crashUploadConsent = value;
        saveKey(QStringLiteral("diagnostics/crashUploadConsent"), value);
        emit crashUploadConsentChanged();
    }
}

void AppSettings::setRememberKey(bool value) {
    if (m_rememberKey != value) {
        m_rememberKey = value;
        saveKey(QStringLiteral("agent/rememberKey"), value);
        emit rememberKeyChanged();
    }
}

void AppSettings::setAutoRefresh(bool value) {
    if (m_autoRefresh != value) {
        m_autoRefresh = value;
        saveKey(QStringLiteral("engine/autoRefresh"), value);
        emit autoRefreshChanged();
    }
}

void AppSettings::setTestingConnection(bool testing) {
    if (m_testingConnection != testing) {
        m_testingConnection = testing;
        emit connectionTestChanged();
    }
}

void AppSettings::setConnectionTestResult(bool ok, const QString &message) {
    m_testingConnection = false;
    m_connectionTestOk = ok;
    m_connectionTestResult = message;
    emit connectionTestChanged();
}

void AppSettings::setWatchExpressions(const QStringList &value) {
    if (m_watchExpressions != value) {
        m_watchExpressions = value;
        saveKey(QStringLiteral("workspace/watchExpressions"), value);
        emit watchExpressionsChanged();
    }
}

void AppSettings::addRecentProject(const QString &path) {
    const QString cleaned = QDir::cleanPath(path.trimmed());
    if (cleaned.isEmpty()) {
        return;
    }
    m_recentProjects.removeAll(cleaned);
    m_recentProjects.prepend(cleaned);
    while (m_recentProjects.size() > 10) {
        m_recentProjects.removeLast();
    }
    saveKey(QStringLiteral("workspace/recentProjects"), m_recentProjects);
    emit recentProjectsChanged();
}

void AppSettings::clearRecentProjects() {
    if (!m_recentProjects.isEmpty()) {
        m_recentProjects.clear();
        saveKey(QStringLiteral("workspace/recentProjects"), m_recentProjects);
        emit recentProjectsChanged();
    }
}

void AppSettings::resetToDefaults() {
    if (!m_ephemeral) {
        m_settings.clear();
        m_settings.sync();
    }
    loadFromStorage();
    emit themeChanged();
    emit languageChanged();
    emit projectDirChanged();
    emit providerChanged();
    emit modelChanged();
    emit baseUrlChanged();
    emit apiKeyChanged();
    emit interlockModeChanged();
    emit mockLlmChanged();
    emit autoStartEngineChanged();
    emit telemetryIntervalMsChanged();
    emit fontSizeChanged();
    emit editorFontSizeChanged();
    emit sidebarVisibleChanged();
    emit bottomPanelVisibleChanged();
    emit rightPanelVisibleChanged();
    emit sidebarWidthChanged();
    emit bottomPanelHeightChanged();
    emit onboardingCompletedChanged();
    emit probeBackendChanged();
    emit uartPortChanged();
    emit uartBaudrateChanged();
    emit autoCheckUpdatesChanged();
    emit updateChannelChanged();
    emit updateUrlChanged();
    emit crashUploadConsentChanged();
    emit rememberKeyChanged();
    emit autoRefreshChanged();
    emit recentProjectsChanged();
    emit watchExpressionsChanged();
}

void AppSettings::sync() {
    if (!m_ephemeral) {
        m_settings.sync();
    }
}
