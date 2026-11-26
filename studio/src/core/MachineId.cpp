#include "core/MachineId.h"

#include <QCryptographicHash>
#include <QFile>
#include <QHostInfo>
#include <QSysInfo>

#ifdef Q_OS_WIN
#include <QSettings>
#endif

namespace {

QByteArray readRawMachineToken() {
#ifdef Q_OS_LINUX
    for (const char *path : {"/etc/machine-id", "/var/lib/dbus/machine-id"}) {
        QFile f(QString::fromLatin1(path));
        if (f.open(QIODevice::ReadOnly)) {
            const QByteArray raw = f.readAll().trimmed();
            if (!raw.isEmpty()) {
                return raw;
            }
        }
    }
#endif
#ifdef Q_OS_WIN
    QSettings reg(QStringLiteral("HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Cryptography"),
                  QSettings::NativeFormat);
    const QString guid = reg.value(QStringLiteral("MachineGuid")).toString().trimmed();
    if (!guid.isEmpty()) {
        return guid.toUtf8();
    }
#endif
    const QByteArray sysId = QSysInfo::machineUniqueId().trimmed();
    if (!sysId.isEmpty()) {
        return sysId;
    }
    return (QHostInfo::localHostName() + QLatin1Char(':') + QSysInfo::machineHostName()).toUtf8();
}

} // namespace

QString MachineId::current() {
    static const QString cached = []() {
        QCryptographicHash hash(QCryptographicHash::Sha256);
        hash.addData("mcu-harness-studio-machine-v1:");
        hash.addData(readRawMachineToken());
        return QString::fromLatin1(hash.result().toHex());
    }();
    return cached;
}

QString MachineId::normalizeHash(const QString &raw) {
    const QString trimmed = raw.trimmed();
    if (trimmed.isEmpty() || trimmed.compare(QStringLiteral("none"), Qt::CaseInsensitive) == 0) {
        return QString();
    }
    if (trimmed.size() == 64) {
        bool allHex = true;
        for (const QChar ch : trimmed) {
            const ushort u = ch.unicode();
            const bool isHex = (u >= '0' && u <= '9') || (u >= 'a' && u <= 'f') || (u >= 'A' && u <= 'F');
            if (!isHex) {
                allHex = false;
                break;
            }
        }
        if (allHex) {
            return trimmed.toLower();
        }
    }
    return QString::fromLatin1(QCryptographicHash::hash(trimmed.toUtf8(), QCryptographicHash::Sha256).toHex());
}
