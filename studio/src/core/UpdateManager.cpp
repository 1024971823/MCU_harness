#include "core/UpdateManager.h"
#include "BuildConfig.h"

extern "C" {
#include "tweetnacl.h"
}

#include <QCryptographicHash>
#include <QDesktopServices>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QProcess>
#include <QStandardPaths>
#include <QSysInfo>
#include <QUrl>

namespace {

struct SemverParts {
    int major = 0;
    int minor = 0;
    int patch = 0;
    QString prerelease;
    bool valid = false;
};

SemverParts parseSemver(const QString &raw) {
    SemverParts out;
    QString s = raw.trimmed();
    if (s.startsWith(QLatin1Char('v'), Qt::CaseInsensitive)) {
        s = s.mid(1);
    }
    const int plusIdx = s.indexOf(QLatin1Char('+'));
    if (plusIdx >= 0) {
        s = s.left(plusIdx);
    }
    const int dashIdx = s.indexOf(QLatin1Char('-'));
    if (dashIdx >= 0) {
        out.prerelease = s.mid(dashIdx + 1);
        s = s.left(dashIdx);
    }
    const QStringList nums = s.split(QLatin1Char('.'));
    if (nums.isEmpty()) {
        return out;
    }
    bool ok0 = false;
    out.major = nums.at(0).toInt(&ok0);
    if (!ok0) {
        return out;
    }
    if (nums.size() > 1) {
        bool ok1 = false;
        out.minor = nums.at(1).toInt(&ok1);
        if (!ok1) {
            return out;
        }
    }
    if (nums.size() > 2) {
        bool ok2 = false;
        out.patch = nums.at(2).toInt(&ok2);
        if (!ok2) {
            return out;
        }
    }
    out.valid = true;
    return out;
}

} // namespace

UpdateManager::UpdateManager(QObject *parent)
    : UpdateManager(QStringLiteral(MCU_STUDIO_UPDATE_PUBKEY_B64),
                    QStringLiteral(MCU_STUDIO_VERSION),
                    defaultDownloadDir(),
                    parent) {}

UpdateManager::UpdateManager(const QString &pubkeyB64,
                             const QString &currentVersion,
                             const QString &downloadDir,
                             QObject *parent)
    : QObject(parent)
    , m_pubkeyB64(pubkeyB64.trimmed())
    , m_currentVersion(currentVersion.trimmed())
    , m_downloadDir(downloadDir.isEmpty() ? defaultDownloadDir() : downloadDir)
    , m_manifestUrl(QStringLiteral(MCU_STUDIO_UPDATE_URL)) {}

QString UpdateManager::defaultDownloadDir() {
    QString base = QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation);
    if (base.isEmpty()) {
        base = QDir::tempPath() + QStringLiteral("/mcu-harness-studio");
    }
    return base + QStringLiteral("/updates");
}

QString UpdateManager::currentPlatformAssetKey() {
    const QString arch = QSysInfo::currentCpuArchitecture();
#if defined(Q_OS_WIN)
    return arch.contains(QStringLiteral("arm")) ? QStringLiteral("windows-arm64") : QStringLiteral("windows-x64");
#elif defined(Q_OS_MACOS)
    return arch.contains(QStringLiteral("arm")) ? QStringLiteral("macos-arm64") : QStringLiteral("macos-x64");
#else
    return arch.contains(QStringLiteral("arm")) || arch.contains(QStringLiteral("aarch64"))
               ? QStringLiteral("linux-arm64")
               : QStringLiteral("linux-x64");
#endif
}

void UpdateManager::setManifestUrl(const QString &url) {
    const QString trimmed = url.trimmed();
    if (m_manifestUrl != trimmed) {
        m_manifestUrl = trimmed;
        emit manifestUrlChanged();
    }
}

int UpdateManager::compareSemver(const QString &a, const QString &b) {
    const SemverParts pa = parseSemver(a);
    const SemverParts pb = parseSemver(b);
    if (!pa.valid || !pb.valid) {
        return QString::compare(a.trimmed(), b.trimmed());
    }
    if (pa.major != pb.major) {
        return pa.major < pb.major ? -1 : 1;
    }
    if (pa.minor != pb.minor) {
        return pa.minor < pb.minor ? -1 : 1;
    }
    if (pa.patch != pb.patch) {
        return pa.patch < pb.patch ? -1 : 1;
    }
    if (pa.prerelease.isEmpty() && !pb.prerelease.isEmpty()) {
        return 1;
    }
    if (!pa.prerelease.isEmpty() && pb.prerelease.isEmpty()) {
        return -1;
    }
    if (pa.prerelease != pb.prerelease) {
        return pa.prerelease < pb.prerelease ? -1 : 1;
    }
    return 0;
}

bool UpdateManager::verifyManifestSignature(const QByteArray &manifestBytes, const QByteArray &sigBytesOrB64) const {
    QByteArray sigBytes = sigBytesOrB64;
    if (sigBytes.size() != crypto_sign_BYTES) {
        sigBytes = QByteArray::fromBase64(sigBytesOrB64.trimmed());
    }
    if (sigBytes.size() != crypto_sign_BYTES) {
        return false;
    }

    const QByteArray pubBytes = QByteArray::fromBase64(m_pubkeyB64.toLatin1());
    if (pubBytes.size() != crypto_sign_PUBLICKEYBYTES) {
        return false;
    }

    QByteArray signedMsg;
    signedMsg.reserve(sigBytes.size() + manifestBytes.size());
    signedMsg.append(sigBytes);
    signedMsg.append(manifestBytes);

    QByteArray opened(signedMsg.size(), '\0');
    unsigned long long openedLen = 0;
    const int rc = crypto_sign_open(
        reinterpret_cast<unsigned char *>(opened.data()),
        &openedLen,
        reinterpret_cast<const unsigned char *>(signedMsg.constData()),
        static_cast<unsigned long long>(signedMsg.size()),
        reinterpret_cast<const unsigned char *>(pubBytes.constData()));
    return rc == 0 && static_cast<qsizetype>(openedLen) == manifestBytes.size();
}

bool UpdateManager::verifyFileSha256AndSize(const QString &filePath,
                                            const QString &expectedSha256Hex,
                                            qint64 expectedSize) {
    QFile f(filePath);
    if (!f.open(QIODevice::ReadOnly)) {
        return false;
    }
    if (expectedSize > 0 && f.size() != expectedSize) {
        return false;
    }
    QCryptographicHash hash(QCryptographicHash::Sha256);
    if (!hash.addData(&f)) {
        return false;
    }
    const QString actualHex = QString::fromLatin1(hash.result().toHex()).toLower();
    return actualHex == expectedSha256Hex.trimmed().toLower();
}

void UpdateManager::checkForUpdates(const QString &manifestUrlOverride) {
    if (m_checking) {
        return;
    }
    const QString targetUrlStr = manifestUrlOverride.trimmed().isEmpty()
                                     ? m_manifestUrl.trimmed()
                                     : manifestUrlOverride.trimmed();
    if (targetUrlStr.isEmpty()) {
        m_errorMessage = tr("Update feed URL is not configured.");
        m_statusMessage = m_errorMessage;
        emit stateChanged();
        emit updateCheckFinished(false, m_errorMessage);
        return;
    }

    const QUrl manifestUrl = QUrl::fromUserInput(targetUrlStr);
    const QUrl sigUrl = QUrl::fromUserInput(targetUrlStr + QStringLiteral(".sig"));
    if (!manifestUrl.isValid() || !sigUrl.isValid()) {
        m_errorMessage = tr("Invalid update manifest URL.");
        m_statusMessage = m_errorMessage;
        emit stateChanged();
        emit updateCheckFinished(false, m_errorMessage);
        return;
    }

    m_checking = true;
    m_errorMessage.clear();
    m_statusMessage = tr("Checking for updates...");
    emit stateChanged();

    QNetworkReply *manifestReply = m_nam.get(QNetworkRequest(manifestUrl));
    QNetworkReply *sigReply = m_nam.get(QNetworkRequest(sigUrl));

    struct PendingCheck {
        bool manifestDone = false;
        bool sigDone = false;
        QByteArray manifestBytes;
        QByteArray sigBytes;
        QString error;
    };
    auto state = std::make_shared<PendingCheck>();

    auto finalizeIfReady = [this, state]() {
        if (!state->manifestDone || !state->sigDone) {
            return;
        }
        m_checking = false;
        if (!state->error.isEmpty()) {
            m_errorMessage = state->error;
            m_statusMessage = m_errorMessage;
            m_updateAvailable = false;
            emit stateChanged();
            emit updateCheckFinished(false, m_errorMessage);
            return;
        }
        handleManifestAndSigFetched(state->manifestBytes, state->sigBytes);
    };

    connect(manifestReply, &QNetworkReply::finished, this, [manifestReply, state, finalizeIfReady]() {
        manifestReply->deleteLater();
        state->manifestDone = true;
        if (manifestReply->error() != QNetworkReply::NoError) {
            if (state->error.isEmpty()) {
                state->error = manifestReply->errorString();
            }
        } else {
            state->manifestBytes = manifestReply->readAll();
        }
        finalizeIfReady();
    });

    connect(sigReply, &QNetworkReply::finished, this, [sigReply, state, finalizeIfReady]() {
        sigReply->deleteLater();
        state->sigDone = true;
        if (sigReply->error() != QNetworkReply::NoError) {
            if (state->error.isEmpty()) {
                state->error = tr("Signature download failed: %1").arg(sigReply->errorString());
            }
        } else {
            state->sigBytes = sigReply->readAll();
        }
        finalizeIfReady();
    });
}

void UpdateManager::handleManifestAndSigFetched(const QByteArray &manifestBytes, const QByteArray &sigBytes) {
    if (!verifyManifestSignature(manifestBytes, sigBytes)) {
        m_updateAvailable = false;
        m_errorMessage = tr("Update manifest Ed25519 signature verification failed.");
        m_statusMessage = m_errorMessage;
        emit stateChanged();
        emit updateCheckFinished(false, m_errorMessage);
        return;
    }

    QJsonParseError err{};
    const QJsonDocument doc = QJsonDocument::fromJson(manifestBytes, &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject()) {
        m_updateAvailable = false;
        m_errorMessage = tr("Malformed update manifest JSON.");
        m_statusMessage = m_errorMessage;
        emit stateChanged();
        emit updateCheckFinished(false, m_errorMessage);
        return;
    }

    const QJsonObject obj = doc.object();
    m_latestVersion = obj.value(QStringLiteral("version")).toString().trimmed();
    m_publishedAt = obj.value(QStringLiteral("released")).toString();
    if (m_publishedAt.isEmpty()) {
        m_publishedAt = obj.value(QStringLiteral("published_at")).toString();
    }
    m_isMandatory = obj.value(QStringLiteral("mandatory")).toBool(false);

    const QJsonValue notesVal = obj.value(QStringLiteral("notes"));
    if (notesVal.isObject()) {
        const QJsonObject notesObj = notesVal.toObject();
        m_releaseNotes = notesObj.value(m_language).toString();
        if (m_releaseNotes.isEmpty()) {
            m_releaseNotes = notesObj.value(QStringLiteral("en")).toString();
        }
        if (m_releaseNotes.isEmpty()) {
            m_releaseNotes = notesObj.value(QStringLiteral("zh_CN")).toString();
        }
    } else {
        m_releaseNotes = notesVal.toString();
        if (m_releaseNotes.isEmpty()) {
            m_releaseNotes = obj.value(QStringLiteral("release_notes")).toString();
        }
    }

    m_downloadUrl.clear();
    m_expectedSha256.clear();
    m_expectedSize = 0;

    const QJsonObject assetsObj = obj.value(QStringLiteral("assets")).toObject();
    if (!assetsObj.isEmpty()) {
        const QString prefKey = currentPlatformAssetKey();
        QJsonObject asset = assetsObj.value(prefKey).toObject();
        if (asset.isEmpty()) {
            const QString firstKey = assetsObj.keys().value(0);
            asset = assetsObj.value(firstKey).toObject();
        }
        m_downloadUrl = asset.value(QStringLiteral("url")).toString().trimmed();
        m_expectedSha256 = asset.value(QStringLiteral("sha256")).toString().trimmed();
        m_expectedSize = asset.value(QStringLiteral("size")).toInteger(0);
    } else {
        m_downloadUrl = obj.value(QStringLiteral("url")).toString().trimmed();
        m_expectedSha256 = obj.value(QStringLiteral("sha256")).toString().trimmed();
        m_expectedSize = obj.value(QStringLiteral("size")).toInteger(0);
    }

    m_updateAvailable = (!m_latestVersion.isEmpty() && compareSemver(m_latestVersion, m_currentVersion) > 0);
    m_errorMessage.clear();
    m_statusMessage = m_updateAvailable
                          ? tr("Version %1 is available.").arg(m_latestVersion)
                          : tr("You are on the latest version (%1).").arg(m_currentVersion);

    emit stateChanged();
    emit updateCheckFinished(m_updateAvailable, QString());
}

void UpdateManager::downloadUpdate() {
    if (m_downloading || m_downloadUrl.isEmpty()) {
        return;
    }

    const QUrl url = QUrl::fromUserInput(m_downloadUrl);
    if (!url.isValid()) {
        m_errorMessage = tr("Invalid download URL.");
        m_statusMessage = m_errorMessage;
        emit stateChanged();
        emit downloadFinished(false, QString(), m_errorMessage);
        return;
    }

    QDir().mkpath(m_downloadDir);
    QString fileName = QFileInfo(url.path()).fileName();
    if (fileName.isEmpty()) {
        fileName = QStringLiteral("mcu-harness-studio-%1.bin").arg(m_latestVersion.isEmpty() ? QStringLiteral("update") : m_latestVersion);
    }
    const QString destPath = QDir(m_downloadDir).filePath(fileName);

    m_downloading = true;
    m_downloadProgress = 0.0;
    m_downloadedFilePath.clear();
    m_errorMessage.clear();
    m_statusMessage = tr("Downloading update...");
    emit downloadProgressChanged();
    emit stateChanged();

    QNetworkReply *reply = m_nam.get(QNetworkRequest(url));
    m_activeDownloadReply = reply;

    connect(reply, &QNetworkReply::downloadProgress, this, [this](qint64 recvd, qint64 total) {
        if (total > 0) {
            m_downloadProgress = qBound(0.0, static_cast<double>(recvd) / static_cast<double>(total), 1.0);
            emit downloadProgressChanged();
        }
    });

    connect(reply, &QNetworkReply::finished, this, [this, reply, destPath]() {
        m_activeDownloadReply = nullptr;
        reply->deleteLater();
        m_downloading = false;

        if (reply->error() != QNetworkReply::NoError) {
            m_errorMessage = reply->errorString();
            m_statusMessage = m_errorMessage;
            emit stateChanged();
            emit downloadFinished(false, QString(), m_errorMessage);
            return;
        }

        QFile out(destPath);
        if (!out.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
            m_errorMessage = tr("Failed to write downloaded update to %1").arg(destPath);
            m_statusMessage = m_errorMessage;
            emit stateChanged();
            emit downloadFinished(false, QString(), m_errorMessage);
            return;
        }
        out.write(reply->readAll());
        out.close();

        if (!m_expectedSha256.isEmpty() && !verifyFileSha256AndSize(destPath, m_expectedSha256, m_expectedSize)) {
            QFile::remove(destPath);
            m_errorMessage = tr("Update checksum or file size verification failed.");
            m_statusMessage = m_errorMessage;
            emit stateChanged();
            emit downloadFinished(false, QString(), m_errorMessage);
            return;
        }

        m_downloadProgress = 1.0;
        m_downloadedFilePath = destPath;
        m_statusMessage = tr("Update downloaded and verified.");
        emit downloadProgressChanged();
        emit stateChanged();
        emit downloadFinished(true, destPath, QString());
    });
}

void UpdateManager::installUpdate() {
    if (m_downloadedFilePath.isEmpty() || !QFileInfo::exists(m_downloadedFilePath)) {
        if (!m_downloadUrl.isEmpty()) {
            QDesktopServices::openUrl(QUrl::fromUserInput(m_downloadUrl));
        }
        return;
    }

#if defined(Q_OS_WIN)
    if (m_downloadedFilePath.endsWith(QStringLiteral(".exe"), Qt::CaseInsensitive) ||
        m_downloadedFilePath.endsWith(QStringLiteral(".msi"), Qt::CaseInsensitive)) {
        QProcess::startDetached(m_downloadedFilePath, {});
        return;
    }
#endif
    QDesktopServices::openUrl(QUrl::fromLocalFile(QFileInfo(m_downloadedFilePath).absolutePath()));
}

void UpdateManager::dismissUpdate() {
    m_updateAvailable = false;
    m_statusMessage.clear();
    emit stateChanged();
}

QString UpdateManager::status() const {
    if (m_checking) {
        return QStringLiteral("checking");
    }
    if (m_downloading) {
        return QStringLiteral("downloading");
    }
    if (!m_errorMessage.isEmpty()) {
        return QStringLiteral("error");
    }
    if (!m_downloadedFilePath.isEmpty()) {
        return QStringLiteral("ready");
    }
    return QStringLiteral("idle");
}

void UpdateManager::skipVersion(const QString &version) {
    Q_UNUSED(version);
    dismissUpdate();
}

void UpdateManager::setDemoAvailableState() {
    m_checking = false;
    m_downloading = false;
    m_updateAvailable = true;
    m_latestVersion = QStringLiteral("1.2.0");
    m_publishedAt = QStringLiteral("2026-11-20T10:00:00Z");
    m_releaseNotes = (m_language == QStringLiteral("zh_CN"))
                         ? QStringLiteral("- 提升 DWARF Live Watch 高频轮询性能\n- 新增 Cortex-M33 TrustZone 异常位域解析\n- 优化 J-Link RTT 环形日志缓冲区稳定性")
                         : QStringLiteral("- Faster DWARF Live Watch polling loop\n- Added Cortex-M33 TrustZone SFSR fault decoder\n- Improved J-Link RTT ring buffer stability");
    m_downloadUrl = QStringLiteral("https://releases.mcuharness.dev/studio/1.2.0/mcu-harness-studio-1.2.0-x86_64.AppImage");
    m_expectedSha256 = QStringLiteral("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    m_expectedSize = 48234496;
    m_errorMessage.clear();
    m_statusMessage = tr("Version %1 is available.").arg(m_latestVersion);
    emit stateChanged();
}
