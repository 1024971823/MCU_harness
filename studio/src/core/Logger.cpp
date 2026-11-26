#include "core/Logger.h"

#include <QDate>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QMutex>
#include <QMutexLocker>
#include <QStandardPaths>
#include <QTextStream>

#include <atomic>
#include <cstdio>

namespace {

QMutex g_logMutex;
QFile *g_logFile = nullptr;
QString g_currentDateStr;
std::atomic<bool> g_verbose{false};
std::atomic<bool> g_echoStderr{true};
QtMessageHandler g_prevHandler = nullptr;

QString ensureLogDirPath() {
    QString base = QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation);
    if (base.isEmpty()) {
        base = QDir::tempPath() + QStringLiteral("/mcu-harness-studio");
    }
    QDir dir(base + QStringLiteral("/logs"));
    if (!dir.exists()) {
        dir.mkpath(QStringLiteral("."));
    }
    return dir.absolutePath();
}

void rotateAndPruneLogsLocked(const QString &todayStr) {
    if (g_logFile && g_currentDateStr == todayStr && g_logFile->isOpen()) {
        return;
    }

    if (g_logFile) {
        g_logFile->flush();
        g_logFile->close();
        delete g_logFile;
        g_logFile = nullptr;
    }

    const QString logDir = ensureLogDirPath();
    const QString filePath = QStringLiteral("%1/studio-%2.log").arg(logDir, todayStr);
    g_logFile = new QFile(filePath);
    if (g_logFile->open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
        g_currentDateStr = todayStr;
    } else {
        delete g_logFile;
        g_logFile = nullptr;
    }

    // Prune logs older than 14 days
    QDir dir(logDir);
    const QFileInfoList entries = dir.entryInfoList(
        QStringList{QStringLiteral("studio-*.log")},
        QDir::Files,
        QDir::Name | QDir::Reversed);
    const QDate cutoff = QDate::currentDate().addDays(-14);
    for (int i = 0; i < entries.size(); ++i) {
        const QFileInfo &fi = entries.at(i);
        if (i >= 14 && fi.lastModified().date() < cutoff) {
            QFile::remove(fi.absoluteFilePath());
        }
    }
}

const char *levelTag(QtMsgType type) {
    switch (type) {
    case QtDebugMsg:
        return "DEBUG";
    case QtInfoMsg:
        return "INFO";
    case QtWarningMsg:
        return "WARN";
    case QtCriticalMsg:
        return "ERROR";
    case QtFatalMsg:
        return "FATAL";
    }
    return "INFO";
}

void messageHandler(QtMsgType type, const QMessageLogContext &ctx, const QString &msg) {
    if (type == QtDebugMsg && !g_verbose.load(std::memory_order_relaxed)) {
        return;
    }

    const QDateTime now = QDateTime::currentDateTimeUtc();
    const QString ts = now.toString(QStringLiteral("yyyy-MM-ddTHH:mm:ss.zzzZ"));
    const QString dateStr = now.date().toString(QStringLiteral("yyyyMMdd"));
    const char *cat = (ctx.category && ctx.category[0] != '\0') ? ctx.category : "default";
    const QString formatted = QStringLiteral("[%1] [%2] [%3] %4\n")
                                  .arg(ts, QLatin1String(levelTag(type)), QLatin1String(cat), msg);

    {
        QMutexLocker lock(&g_logMutex);
        rotateAndPruneLogsLocked(dateStr);
        if (g_logFile && g_logFile->isOpen()) {
            const QByteArray utf8 = formatted.toUtf8();
            g_logFile->write(utf8);
            g_logFile->flush();
        }
    }

    if (g_echoStderr.load(std::memory_order_relaxed)) {
        const QByteArray utf8 = formatted.toUtf8();
        std::fwrite(utf8.constData(), 1, static_cast<size_t>(utf8.size()), stderr);
        std::fflush(stderr);
    }

    if (type == QtFatalMsg) {
        abort();
    }
}

} // namespace

void Logger::install() {
    QMutexLocker lock(&g_logMutex);
    const QString todayStr = QDateTime::currentDateTimeUtc().date().toString(QStringLiteral("yyyyMMdd"));
    rotateAndPruneLogsLocked(todayStr);
    if (!g_prevHandler) {
        g_prevHandler = qInstallMessageHandler(messageHandler);
    }
}

void Logger::uninstall() {
    QMutexLocker lock(&g_logMutex);
    if (g_prevHandler) {
        qInstallMessageHandler(g_prevHandler);
        g_prevHandler = nullptr;
    } else {
        qInstallMessageHandler(nullptr);
    }
    if (g_logFile) {
        g_logFile->flush();
        g_logFile->close();
        delete g_logFile;
        g_logFile = nullptr;
    }
}

void Logger::setVerbose(bool verbose) {
    g_verbose.store(verbose, std::memory_order_relaxed);
}

void Logger::setEchoToStderr(bool echo) {
    g_echoStderr.store(echo, std::memory_order_relaxed);
}

QString Logger::logDirectory() {
    return ensureLogDirPath();
}

QString Logger::currentLogFile() {
    const QString todayStr = QDateTime::currentDateTimeUtc().date().toString(QStringLiteral("yyyyMMdd"));
    return QStringLiteral("%1/studio-%2.log").arg(ensureLogDirPath(), todayStr);
}

QStringList Logger::recentLogFiles(int maxCount) {
    QDir dir(ensureLogDirPath());
    const QFileInfoList entries = dir.entryInfoList(
        QStringList{QStringLiteral("studio-*.log")},
        QDir::Files,
        QDir::Time);
    QStringList out;
    for (int i = 0; i < entries.size() && i < maxCount; ++i) {
        out.append(entries.at(i).absoluteFilePath());
    }
    return out;
}
