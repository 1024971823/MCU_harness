#include "app/AppInfo.h"
#include "BuildConfig.h"
#include "core/MachineId.h"

#include <QCoreApplication>
#include <QFile>
#include <QSysInfo>

AppInfo::AppInfo(bool demoMode, bool screenshotMode, QObject *parent)
    : QObject(parent)
    , m_demoMode(demoMode)
    , m_screenshotMode(screenshotMode)
    , m_machineId(MachineId::current()) {}

QString AppInfo::version() const {
    return QStringLiteral(MCU_STUDIO_VERSION);
}

void AppInfo::setEngineVersion(const QString &v) {
    const QString cleaned = v.trimmed();
    if (!cleaned.isEmpty() && m_engineVersion != cleaned) {
        m_engineVersion = cleaned;
        emit engineVersionChanged();
    }
}

QString AppInfo::thirdPartyLicenses() const {
    static const QString cached = []() {
        QString out;
        auto appendRes = [&](const QString &title, const QString &qrcPath, const QString &diskFallback) {
            QByteArray bytes;
            QFile f1(qrcPath);
            if (f1.open(QIODevice::ReadOnly)) {
                bytes = f1.readAll();
            } else {
                QFile f2(diskFallback);
                if (f2.open(QIODevice::ReadOnly)) {
                    bytes = f2.readAll();
                }
            }
            if (!bytes.isEmpty()) {
                out += QStringLiteral("=== %1 ===\n%2\n\n").arg(title, QString::fromUtf8(bytes).trimmed());
            }
        };
        appendRes(QStringLiteral("Inter Font (SIL Open Font License 1.1)"),
                  QStringLiteral(":/qt/qml/McuStudio/resources/fonts/Inter-OFL.txt"),
                  QStringLiteral(MCU_STUDIO_SOURCE_DIR "/resources/fonts/Inter-OFL.txt"));
        appendRes(QStringLiteral("JetBrains Mono Font (SIL Open Font License 1.1)"),
                  QStringLiteral(":/qt/qml/McuStudio/resources/fonts/JetBrainsMono-OFL.txt"),
                  QStringLiteral(MCU_STUDIO_SOURCE_DIR "/resources/fonts/JetBrainsMono-OFL.txt"));
        appendRes(QStringLiteral("Lucide Icons (ISC License)"),
                  QStringLiteral(":/qt/qml/McuStudio/resources/icons/LUCIDE-LICENSE.txt"),
                  QStringLiteral(MCU_STUDIO_SOURCE_DIR "/resources/icons/LUCIDE-LICENSE.txt"));
        out += QStringLiteral("=== TweetNaCl ===\nPublic Domain (D. J. Bernstein, B. van Gastel, W. Janssen, T. Lange, P. Schwabe, S. Smetsers)\n");
        return out;
    }();
    return cached;
}

QString AppInfo::gitHash() const {
    return QStringLiteral(MCU_STUDIO_GIT_HASH);
}

QString AppInfo::buildDate() const {
    return QStringLiteral(MCU_STUDIO_BUILD_DATE);
}

QString AppInfo::qtVersion() const {
    return QString::fromLatin1(qVersion());
}

QString AppInfo::osName() const {
    return QSysInfo::prettyProductName();
}

QString AppInfo::appName() const {
    return QCoreApplication::applicationName();
}

QString AppInfo::organization() const {
    return QCoreApplication::organizationName();
}

QString AppInfo::domain() const {
    return QCoreApplication::organizationDomain();
}

QString AppInfo::machineId() const {
    return m_machineId;
}

bool AppInfo::demoMode() const {
    return m_demoMode;
}

bool AppInfo::screenshotMode() const {
    return m_screenshotMode;
}
