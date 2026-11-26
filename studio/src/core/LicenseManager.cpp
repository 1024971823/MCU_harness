#include "core/LicenseManager.h"
#include "BuildConfig.h"
#include "core/MachineId.h"

extern "C" {
#include "tweetnacl.h"
}

#include <QByteArray>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QMessageAuthenticationCode>
#include <QRegularExpression>
#include <QStandardPaths>
#include <QTimeZone>

namespace {

constexpr qint64 kTrialDurationDays = 14;
constexpr qint64 kClockRollbackToleranceSecs = 300;

QString defaultStorageDir() {
    QString base = QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation);
    if (base.isEmpty()) {
        base = QDir::tempPath() + QStringLiteral("/mcu-harness-studio");
    }
    return base + QStringLiteral("/license");
}

bool decodeBase64UrlSegment(const QString &seg, QByteArray &out) {
    static const QRegularExpression re(QStringLiteral("^[A-Za-z0-9\\-_]+$"));
    if (seg.isEmpty() || !re.match(seg).hasMatch()) {
        return false;
    }
    const auto res = QByteArray::fromBase64Encoding(
        seg.toLatin1(), QByteArray::Base64UrlEncoding | QByteArray::OmitTrailingEquals);
    if (res.decodingStatus != QByteArray::Base64DecodingStatus::Ok) {
        return false;
    }
    out = res.decoded;
    return true;
}

} // namespace

LicenseManager::LicenseManager(QObject *parent)
    : LicenseManager(QStringLiteral(MCU_STUDIO_LICENSE_PUBKEY_B64),
                     defaultStorageDir(),
                     MachineId::current(),
                     parent) {}

LicenseManager::LicenseManager(const QString &pubkeyB64,
                               const QString &storageDir,
                               const QString &machineIdOverride,
                               QObject *parent)
    : QObject(parent)
    , m_pubkeyB64(pubkeyB64.trimmed())
    , m_storageDir(storageDir)
    , m_machineId(machineIdOverride.isEmpty() ? MachineId::current() : machineIdOverride) {
    QDir().mkpath(m_storageDir);
    evaluateState();
}

void LicenseManager::setNowEpochOverride(qint64 epochSeconds) {
    m_nowEpochOverride = epochSeconds;
}

void LicenseManager::setMachineIdOverride(const QString &machineId) {
    m_machineId = machineId.isEmpty() ? MachineId::current() : machineId;
    evaluateState();
    emit licenseChanged();
}

qint64 LicenseManager::currentEpochSeconds() const {
    if (m_nowEpochOverride >= 0) {
        return m_nowEpochOverride;
    }
    return QDateTime::currentSecsSinceEpoch();
}

QDate LicenseManager::currentDate() const {
    if (m_nowEpochOverride >= 0) {
        return QDateTime::fromSecsSinceEpoch(m_nowEpochOverride, QTimeZone::utc()).date();
    }
    return QDate::currentDate();
}

QString LicenseManager::licenseFilePath() const {
    return QDir(m_storageDir).filePath(QStringLiteral("license.lic"));
}

QString LicenseManager::trialFilePath() const {
    return QDir(m_storageDir).filePath(QStringLiteral("trial.json"));
}

QByteArray LicenseManager::trialHmac(qint64 startedAt, qint64 lastSeen) const {
    const QByteArray key = QCryptographicHash::hash(
        QByteArrayLiteral("mcuh-trial-hmac-v1:") + m_machineId.toUtf8(),
        QCryptographicHash::Sha256);
    const QByteArray msg = QStringLiteral("%1:%2:%3").arg(startedAt).arg(lastSeen).arg(m_machineId).toUtf8();
    return QMessageAuthenticationCode::hash(msg, key, QCryptographicHash::Sha256).toHex();
}

void LicenseManager::clearLicenseFields() {
    m_holder.clear();
    m_email.clear();
    m_licenseId.clear();
    m_seats = 1;
    m_expiresAt.clear();
    m_isPerpetual = false;
    m_features.clear();
}

bool LicenseManager::verifyToken(const QString &token,
                                 QJsonObject *outPayload,
                                 QString *outError,
                                 const QDate &todayOverride) const {
    auto setErr = [&](const QString &msg) {
        if (outError) {
            *outError = msg;
        }
        return false;
    };

    const QString trimmed = token.trimmed();
    const QStringList parts = trimmed.split(QLatin1Char('.'));
    if (parts.size() != 3 || parts.at(0) != QStringLiteral("MCUH1")) {
        return setErr(tr("Malformed license format (expected MCUH1.<payload>.<sig>)."));
    }

    QByteArray payloadBytes;
    if (!decodeBase64UrlSegment(parts.at(1), payloadBytes)) {
        return setErr(tr("Invalid license payload encoding."));
    }

    QByteArray sigBytes;
    if (!decodeBase64UrlSegment(parts.at(2), sigBytes) || sigBytes.size() != crypto_sign_BYTES) {
        return setErr(tr("Invalid license signature encoding."));
    }

    const QByteArray pubBytes = QByteArray::fromBase64(m_pubkeyB64.toLatin1());
    if (pubBytes.size() != crypto_sign_PUBLICKEYBYTES) {
        return setErr(tr("Invalid embedded license public key."));
    }

    QByteArray signedMsg;
    signedMsg.reserve(sigBytes.size() + payloadBytes.size());
    signedMsg.append(sigBytes);
    signedMsg.append(payloadBytes);

    QByteArray opened(signedMsg.size(), '\0');
    unsigned long long openedLen = 0;
    const int rc = crypto_sign_open(
        reinterpret_cast<unsigned char *>(opened.data()),
        &openedLen,
        reinterpret_cast<const unsigned char *>(signedMsg.constData()),
        static_cast<unsigned long long>(signedMsg.size()),
        reinterpret_cast<const unsigned char *>(pubBytes.constData()));
    if (rc != 0 || static_cast<qsizetype>(openedLen) != payloadBytes.size()) {
        return setErr(tr("Invalid Ed25519 license signature."));
    }

    QJsonParseError parseErr{};
    const QJsonDocument doc = QJsonDocument::fromJson(payloadBytes, &parseErr);
    if (parseErr.error != QJsonParseError::NoError || !doc.isObject()) {
        return setErr(tr("Invalid license payload JSON."));
    }

    const QJsonObject obj = doc.object();
    if (obj.value(QStringLiteral("v")).toInt(0) != 1) {
        return setErr(tr("Unsupported license payload version."));
    }

    const QJsonValue machineVal = obj.value(QStringLiteral("machine"));
    if (!machineVal.isNull() && !machineVal.isUndefined()) {
        const QString boundMachine = machineVal.toString().trimmed();
        if (!boundMachine.isEmpty() && boundMachine != QStringLiteral("*")) {
            const QString boundNorm = MachineId::normalizeHash(boundMachine);
            const QString curNorm = MachineId::normalizeHash(m_machineId);
            if (boundNorm != curNorm) {
                return setErr(tr("License is bound to a different machine ID."));
            }
        }
    }

    const QJsonValue expVal = obj.value(QStringLiteral("expires"));
    if (!expVal.isNull() && !expVal.isUndefined()) {
        const QString expStr = expVal.toString().trimmed();
        if (!expStr.isEmpty()) {
            const QDate expDate = QDate::fromString(expStr, Qt::ISODate);
            if (!expDate.isValid()) {
                return setErr(tr("Invalid license expiration date."));
            }
            const QDate refDate = todayOverride.isValid() ? todayOverride : currentDate();
            if (expDate < refDate) {
                if (outPayload) {
                    *outPayload = obj;
                }
                return setErr(tr("License expired on %1.").arg(expStr));
            }
        }
    }

    if (outPayload) {
        *outPayload = obj;
    }
    return true;
}

bool LicenseManager::evaluateTrialState() {
    clearLicenseFields();
    m_isLicensed = false;
    m_tier = QStringLiteral("trial");
    m_features = QStringList{QStringLiteral("agent"), QStringLiteral("hil")};

    const qint64 now = currentEpochSeconds();
    const QString tPath = trialFilePath();
    QFile file(tPath);

    if (!file.exists()) {
        QJsonObject obj;
        obj.insert(QStringLiteral("v"), 1);
        obj.insert(QStringLiteral("started_at"), now);
        obj.insert(QStringLiteral("last_seen"), now);
        obj.insert(QStringLiteral("hmac"), QString::fromLatin1(trialHmac(now, now)));

        if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
            file.write(QJsonDocument(obj).toJson(QJsonDocument::Compact));
            file.close();
        }
        m_status = QStringLiteral("trial");
        m_daysRemaining = static_cast<int>(kTrialDurationDays);
        m_expiresAt = QDateTime::fromSecsSinceEpoch(now + kTrialDurationDays * 86400, QTimeZone::utc())
                          .date()
                          .toString(Qt::ISODate);
        m_isValid = true;
        m_lastError.clear();
        return true;
    }

    if (!file.open(QIODevice::ReadOnly)) {
        m_status = QStringLiteral("invalid");
        m_daysRemaining = 0;
        m_isValid = false;
        m_lastError = tr("Unable to read trial state file.");
        return false;
    }

    const QByteArray raw = file.readAll();
    file.close();

    QJsonParseError err{};
    const QJsonDocument doc = QJsonDocument::fromJson(raw, &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject()) {
        m_status = QStringLiteral("invalid");
        m_daysRemaining = 0;
        m_isValid = false;
        m_lastError = tr("Corrupted trial state file.");
        return false;
    }

    const QJsonObject obj = doc.object();
    const qint64 startedAt = obj.value(QStringLiteral("started_at")).toInteger(-1);
    const qint64 lastSeen = obj.value(QStringLiteral("last_seen")).toInteger(-1);
    const QByteArray storedHmac = obj.value(QStringLiteral("hmac")).toString().toLatin1();
    if (startedAt <= 0 || lastSeen <= 0 || storedHmac.isEmpty()) {
        m_status = QStringLiteral("invalid");
        m_daysRemaining = 0;
        m_isValid = false;
        m_lastError = tr("Incomplete trial metadata.");
        return false;
    }

    const QByteArray expectedHmac = trialHmac(startedAt, lastSeen);
    if (storedHmac != expectedHmac) {
        m_status = QStringLiteral("invalid");
        m_daysRemaining = 0;
        m_isValid = false;
        m_lastError = tr("Trial integrity verification failed (HMAC mismatch).");
        return false;
    }

    if (now + kClockRollbackToleranceSecs < lastSeen) {
        m_status = QStringLiteral("expired");
        m_daysRemaining = 0;
        m_isValid = false;
        m_lastError = tr("System clock rollback detected.");
        return false;
    }

    const qint64 newLastSeen = qMax(lastSeen, now);
    if (newLastSeen != lastSeen) {
        QJsonObject updated = obj;
        updated.insert(QStringLiteral("last_seen"), newLastSeen);
        updated.insert(QStringLiteral("hmac"), QString::fromLatin1(trialHmac(startedAt, newLastSeen)));
        if (file.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
            file.write(QJsonDocument(updated).toJson(QJsonDocument::Compact));
            file.close();
        }
    }

    const qint64 totalTrialSecs = kTrialDurationDays * 86400;
    const qint64 endEpoch = startedAt + totalTrialSecs;
    m_expiresAt = QDateTime::fromSecsSinceEpoch(endEpoch, QTimeZone::utc()).date().toString(Qt::ISODate);

    if (now >= endEpoch) {
        m_status = QStringLiteral("expired");
        m_daysRemaining = 0;
        m_isValid = false;
        m_lastError = tr("14-day evaluation trial has expired.");
        return false;
    }

    const qint64 remainingSecs = endEpoch - now;
    m_daysRemaining = static_cast<int>((remainingSecs + 86399) / 86400);
    m_status = QStringLiteral("trial");
    m_isValid = true;
    m_lastError.clear();
    return true;
}

void LicenseManager::evaluateState() {
    const QString licPath = licenseFilePath();
    QFile f(licPath);
    if (f.exists() && f.open(QIODevice::ReadOnly)) {
        const QString token = QString::fromUtf8(f.readAll()).trimmed();
        f.close();

        QJsonObject payload;
        QString errMsg;
        if (verifyToken(token, &payload, &errMsg)) {
            m_status = QStringLiteral("active");
            m_tier = payload.value(QStringLiteral("edition")).toString(QStringLiteral("pro"));
            m_holder = payload.value(QStringLiteral("licensee")).toString();
            m_email = payload.value(QStringLiteral("email")).toString();
            m_licenseId = payload.value(QStringLiteral("license_id")).toString();
            m_seats = payload.value(QStringLiteral("seats")).toInt(1);

            const QJsonValue expVal = payload.value(QStringLiteral("expires"));
            if (expVal.isNull() || expVal.isUndefined() || expVal.toString().trimmed().isEmpty()) {
                m_isPerpetual = true;
                m_expiresAt.clear();
                m_daysRemaining = -1;
            } else {
                m_isPerpetual = false;
                m_expiresAt = expVal.toString().trimmed();
                const QDate expDate = QDate::fromString(m_expiresAt, Qt::ISODate);
                m_daysRemaining = static_cast<int>(currentDate().daysTo(expDate));
            }

            m_features.clear();
            const QJsonArray feats = payload.value(QStringLiteral("features")).toArray();
            for (const QJsonValue &fv : feats) {
                const QString fs = fv.toString().trimmed();
                if (!fs.isEmpty()) {
                    m_features.append(fs);
                }
            }
            m_isValid = true;
            m_isLicensed = true;
            m_lastError.clear();
            return;
        }

        // Stored license file failed verification (expired, wrong machine, or tampered)
        clearLicenseFields();
        m_isLicensed = false;
        m_isValid = false;
        m_daysRemaining = 0;
        m_lastError = errMsg;
        if (!payload.isEmpty() && errMsg.contains(QStringLiteral("expired"), Qt::CaseInsensitive)) {
            m_status = QStringLiteral("expired");
            m_tier = payload.value(QStringLiteral("edition")).toString(QStringLiteral("pro"));
            m_holder = payload.value(QStringLiteral("licensee")).toString();
            m_email = payload.value(QStringLiteral("email")).toString();
            m_expiresAt = payload.value(QStringLiteral("expires")).toString();
        } else {
            m_status = QStringLiteral("invalid");
            m_tier = QStringLiteral("none");
        }
        return;
    }

    evaluateTrialState();
}

bool LicenseManager::activate(const QString &licenseKey) {
    QString token = licenseKey.trimmed();
    if (token.isEmpty()) {
        m_lastError = tr("License key cannot be empty.");
        emit licenseChanged();
        return false;
    }

    // Also allow pasting a path to a .lic file
    if (!token.startsWith(QStringLiteral("MCUH1.")) && QFileInfo::exists(token)) {
        QFile f(token);
        if (f.open(QIODevice::ReadOnly)) {
            token = QString::fromUtf8(f.readAll()).trimmed();
        }
    }

    QJsonObject payload;
    QString errMsg;
    if (!verifyToken(token, &payload, &errMsg)) {
        m_lastError = errMsg;
        emit licenseChanged();
        return false;
    }

    QDir().mkpath(m_storageDir);
    QFile out(licenseFilePath());
    if (!out.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        m_lastError = tr("Failed to save license file.");
        emit licenseChanged();
        return false;
    }
    out.write(token.toUtf8() + '\n');
    out.close();

    evaluateState();
    emit licenseChanged();
    return true;
}

bool LicenseManager::activateFromFile(const QUrl &fileUrl) {
    const QString localPath = fileUrl.isLocalFile() ? fileUrl.toLocalFile() : fileUrl.toString();
    QFile f(localPath);
    if (!f.open(QIODevice::ReadOnly)) {
        m_lastError = tr("Unable to open license file: %1").arg(localPath);
        emit licenseChanged();
        return false;
    }
    const QString token = QString::fromUtf8(f.readAll()).trimmed();
    return activate(token);
}

void LicenseManager::deactivate() {
    QFile::remove(licenseFilePath());
    evaluateState();
    emit licenseChanged();
}

void LicenseManager::refresh() {
    evaluateState();
    emit licenseChanged();
}

bool LicenseManager::hasFeature(const QString &feature) const {
    if (!m_isValid) {
        return false;
    }
    return m_features.contains(feature);
}
