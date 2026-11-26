#include "core/Utils.h"

#include <QClipboard>
#include <QDateTime>
#include <QDesktopServices>
#include <QFileInfo>
#include <QGuiApplication>
#include <QLocale>

Utils::Utils(QObject *parent)
    : QObject(parent) {}

void Utils::copyToClipboard(const QString &text) const {
    if (QClipboard *cb = QGuiApplication::clipboard()) {
        cb->setText(text);
    }
}

QString Utils::formatBytes(qint64 bytes) const {
    if (bytes < 0) {
        return QStringLiteral("-");
    }
    if (bytes < 1024) {
        return QStringLiteral("%1 B").arg(bytes);
    }
    const double kb = static_cast<double>(bytes) / 1024.0;
    if (kb < 1024.0) {
        return QStringLiteral("%1 KB").arg(kb, 0, 'f', 1);
    }
    const double mb = kb / 1024.0;
    if (mb < 1024.0) {
        return QStringLiteral("%1 MB").arg(mb, 0, 'f', 2);
    }
    const double gb = mb / 1024.0;
    return QStringLiteral("%1 GB").arg(gb, 0, 'f', 2);
}

QString Utils::formatTimestamp(double epochSeconds) const {
    if (epochSeconds <= 0.0) {
        return QStringLiteral("-");
    }
    const qint64 msecs = static_cast<qint64>(epochSeconds * 1000.0);
    return QDateTime::fromMSecsSinceEpoch(msecs).toLocalTime().toString(QStringLiteral("yyyy-MM-dd HH:mm:ss"));
}

QString Utils::formatDurationMs(qint64 ms) const {
    if (ms < 1000) {
        return QStringLiteral("%1 ms").arg(ms);
    }
    const double s = static_cast<double>(ms) / 1000.0;
    if (s < 60.0) {
        return QStringLiteral("%1 s").arg(s, 0, 'f', 1);
    }
    const qint64 mins = ms / 60000;
    const qint64 secs = (ms % 60000) / 1000;
    return QStringLiteral("%1m %2s").arg(mins).arg(secs);
}

QString Utils::basename(const QString &path) const {
    if (path.isEmpty()) {
        return QString();
    }
    return QFileInfo(path).fileName();
}

bool Utils::fileExists(const QString &path) const {
    if (path.isEmpty()) {
        return false;
    }
    return QFileInfo::exists(path);
}

void Utils::openExternalUrl(const QUrl &url) const {
    if (url.isValid()) {
        QDesktopServices::openUrl(url);
    }
}

void Utils::openPathInFileManager(const QString &path) const {
    if (path.isEmpty()) {
        return;
    }
    QFileInfo fi(path);
    const QString target = fi.isDir() ? fi.absoluteFilePath() : fi.absolutePath();
    QDesktopServices::openUrl(QUrl::fromLocalFile(target));
}

QString Utils::urlToLocalFile(const QUrl &url) const {
    if (url.isLocalFile()) {
        return url.toLocalFile();
    }
    return url.toString();
}
