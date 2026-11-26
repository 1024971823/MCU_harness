#include "core/DiagnosticsService.h"
#include "BuildConfig.h"
#include "backend/EngineClient.h"
#include "core/CrashHandler.h"
#include "core/Logger.h"

#include <QDateTime>
#include <QDesktopServices>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QSysInfo>
#include <QTemporaryFile>
#include <QVariantMap>

DiagnosticsService::DiagnosticsService(QObject *parent)
    : QObject(parent) {
    scanCrashReports();
}

QString DiagnosticsService::logDirectory() const {
    return Logger::logDirectory();
}

QString DiagnosticsService::crashDirectory() const {
    return CrashHandler::crashDirectory();
}

QString DiagnosticsService::crashTime() const {
    if (m_pendingCrashes.isEmpty()) {
        return QString();
    }
    return m_pendingCrashes.first().toMap().value(QStringLiteral("timestamp")).toString();
}

QString DiagnosticsService::crashSignal() const {
    if (m_pendingCrashes.isEmpty()) {
        return QString();
    }
    return m_pendingCrashes.first().toMap().value(QStringLiteral("signal")).toString();
}

QString DiagnosticsService::crashBacktrace() const {
    if (m_pendingCrashes.isEmpty()) {
        return QString();
    }
    return m_pendingCrashes.first().toMap().value(QStringLiteral("backtrace")).toString();
}

void DiagnosticsService::dismissCrashReport() {
    if (!m_pendingCrashes.isEmpty()) {
        dismissCrash(m_pendingCrashes.first().toMap().value(QStringLiteral("id")).toString());
    }
}

void DiagnosticsService::sendCrashReport() {
    if (!m_pendingCrashes.isEmpty()) {
        uploadCrash(m_pendingCrashes.first().toMap().value(QStringLiteral("id")).toString());
    }
}

void DiagnosticsService::setBusy(bool b) {
    if (m_busy != b) {
        m_busy = b;
        emit busyChanged();
    }
}

void DiagnosticsService::scanCrashReports() {
    QVariantList list;
    QDir dir(crashDirectory());
    const QFileInfoList entries = dir.entryInfoList(
        QStringList{QStringLiteral("crash-*.json")},
        QDir::Files,
        QDir::Time);

    for (const QFileInfo &fi : entries) {
        const QString ackPath = fi.absoluteFilePath() + QStringLiteral(".ack");
        if (QFileInfo::exists(ackPath)) {
            continue;
        }
        QFile f(fi.absoluteFilePath());
        if (!f.open(QIODevice::ReadOnly)) {
            continue;
        }
        const QJsonDocument doc = QJsonDocument::fromJson(f.readAll());
        f.close();
        if (!doc.isObject()) {
            continue;
        }
        const QJsonObject obj = doc.object();
        QVariantMap item;
        item.insert(QStringLiteral("id"), obj.value(QStringLiteral("id")).toString(fi.completeBaseName()));
        const qint64 ts = obj.value(QStringLiteral("timestamp")).toInteger(0);
        item.insert(QStringLiteral("timestamp"),
                    ts > 0 ? QDateTime::fromSecsSinceEpoch(ts).toLocalTime().toString(QStringLiteral("yyyy-MM-dd HH:mm:ss"))
                           : fi.lastModified().toString(QStringLiteral("yyyy-MM-dd HH:mm:ss")));
        item.insert(QStringLiteral("signal"), obj.value(QStringLiteral("signal")).toString(QStringLiteral("UNKNOWN")));
        item.insert(QStringLiteral("version"), obj.value(QStringLiteral("version")).toString());
        item.insert(QStringLiteral("gitHash"), obj.value(QStringLiteral("gitHash")).toString());
        item.insert(QStringLiteral("platform"), obj.value(QStringLiteral("platform")).toString());
        item.insert(QStringLiteral("filePath"), fi.absoluteFilePath());

        QString btText;
        const QString btPath = obj.value(QStringLiteral("backtraceFile")).toString();
        if (!btPath.isEmpty() && QFileInfo::exists(btPath)) {
            QFile btFile(btPath);
            if (btFile.open(QIODevice::ReadOnly)) {
                btText = QString::fromUtf8(btFile.read(16384));
            }
        }
        item.insert(QStringLiteral("backtrace"), btText);
        list.append(item);
    }

    m_pendingCrashes = list;
    emit crashReportsChanged();
}

void DiagnosticsService::markCrashAcked(const QString &id) {
    QDir dir(crashDirectory());
    const QString jsonPath = dir.filePath(id + QStringLiteral(".json"));
    if (QFileInfo::exists(jsonPath)) {
        QFile ack(jsonPath + QStringLiteral(".ack"));
        if (ack.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
            ack.write(QDateTime::currentDateTimeUtc().toString(Qt::ISODate).toUtf8());
            ack.close();
        }
    }
    scanCrashReports();
}

void DiagnosticsService::dismissCrash(const QString &id) {
    for (int i = 0; i < m_pendingCrashes.size(); ++i) {
        if (m_pendingCrashes.at(i).toMap().value(QStringLiteral("id")).toString() == id) {
            if (m_pendingCrashes.at(i).toMap().value(QStringLiteral("synthetic")).toBool()) {
                m_pendingCrashes.removeAt(i);
                emit crashReportsChanged();
                return;
            }
        }
    }
    markCrashAcked(id);
}

void DiagnosticsService::uploadCrash(const QString &id) {
    QVariantMap targetItem;
    for (const QVariant &v : m_pendingCrashes) {
        const QVariantMap m = v.toMap();
        if (m.value(QStringLiteral("id")).toString() == id) {
            targetItem = m;
            break;
        }
    }
    if (targetItem.isEmpty()) {
        return;
    }

    const QString endpoint = QStringLiteral(MCU_STUDIO_CRASH_URL).trimmed();
    if (endpoint.isEmpty() || targetItem.value(QStringLiteral("synthetic")).toBool()) {
        m_lastStatus = tr("Crash upload endpoint not configured; crash report %1 dismissed locally.").arg(id);
        emit lastStatusChanged();
        dismissCrash(id);
        return;
    }

    setBusy(true);
    m_lastError.clear();
    emit lastErrorChanged();

    QJsonObject payload = QJsonObject::fromVariantMap(targetItem);
    QNetworkRequest req(QUrl::fromUserInput(endpoint));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));

    QNetworkReply *reply = m_nam.post(req, QJsonDocument(payload).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, id]() {
        reply->deleteLater();
        setBusy(false);
        if (reply->error() != QNetworkReply::NoError) {
            m_lastError = tr("Failed to upload crash report: %1").arg(reply->errorString());
            emit lastErrorChanged();
            return;
        }
        m_lastStatus = tr("Crash report %1 uploaded.").arg(id);
        emit lastStatusChanged();
        markCrashAcked(id);
    });
}

void DiagnosticsService::openLogFolder() const {
    QDesktopServices::openUrl(QUrl::fromLocalFile(logDirectory()));
}

void DiagnosticsService::exportBundle(const QUrl &destUrl) {
    QString destPath = destUrl.isLocalFile() ? destUrl.toLocalFile() : destUrl.toString();
    destPath = destPath.trimmed();
    if (destPath.isEmpty()) {
        return;
    }
    if (!destPath.endsWith(QStringLiteral(".zip"), Qt::CaseInsensitive)) {
        destPath += QStringLiteral(".zip");
    }

    QStringList attachmentPaths = Logger::recentLogFiles(3);
    QDir cdir(crashDirectory());
    const QFileInfoList crashes = cdir.entryInfoList(
        QStringList{QStringLiteral("crash-*.json"), QStringLiteral("crash-*.bt")},
        QDir::Files,
        QDir::Time);
    for (int i = 0; i < crashes.size() && i < 6; ++i) {
        attachmentPaths.append(crashes.at(i).absoluteFilePath());
    }

    QString stderrTempPath;
    if (m_engineClient) {
        const QStringList stderrLines = m_engineClient->recentStderrLines();
        if (!stderrLines.isEmpty()) {
            const QString tmpPath = QDir::temp().filePath(
                QStringLiteral("mcu-studio-engine-stderr-%1.log").arg(QDateTime::currentMSecsSinceEpoch()));
            QFile tf(tmpPath);
            if (tf.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
                tf.write(stderrLines.join(QLatin1Char('\n')).toUtf8() + '\n');
                tf.close();
                stderrTempPath = tmpPath;
                attachmentPaths.append(tmpPath);
            }
        }
    }

    if (m_engineClient && m_engineClient->isRunning()) {
        setBusy(true);
        m_lastError.clear();
        emit lastErrorChanged();

        QJsonObject appInfo;
        appInfo.insert(QStringLiteral("version"), QStringLiteral(MCU_STUDIO_VERSION));
        appInfo.insert(QStringLiteral("git_hash"), QStringLiteral(MCU_STUDIO_GIT_HASH));
        appInfo.insert(QStringLiteral("build_date"), QStringLiteral(MCU_STUDIO_BUILD_DATE));
        appInfo.insert(QStringLiteral("qt_version"), QString::fromLatin1(qVersion()));
        appInfo.insert(QStringLiteral("os"), QSysInfo::prettyProductName());

        QJsonObject params;
        params.insert(QStringLiteral("target_path"), destPath);
        params.insert(QStringLiteral("app_info"), appInfo);
        params.insert(QStringLiteral("files"), QJsonArray::fromStringList(attachmentPaths));

        m_engineClient->sendRequest(
            QStringLiteral("export_diagnostics"),
            params,
            [this, destPath, stderrTempPath](bool ok, const QJsonObject &res, const QString &err) {
                if (!stderrTempPath.isEmpty()) {
                    QFile::remove(stderrTempPath);
                }
                setBusy(false);
                if (!ok) {
                    m_lastError = err;
                    emit lastErrorChanged();
                    emit bundleExported(false, destPath, err);
                    return;
                }
                const QString outPath = res.value(QStringLiteral("path")).toString(destPath);
                m_lastExportPath = outPath;
                m_lastStatus = tr("Diagnostics exported to %1").arg(outPath);
                emit lastExportPathChanged();
                emit lastStatusChanged();
                emit bundleExported(true, outPath, QString());
            },
            15000);
        return;
    }

    // Fallback when engine is offline: write JSON summary next to requested path
    if (!stderrTempPath.isEmpty()) {
        QFile::remove(stderrTempPath);
    }
    const QString fallbackPath = destPath + QStringLiteral(".json");
    QJsonObject summary;
    summary.insert(QStringLiteral("version"), QStringLiteral(MCU_STUDIO_VERSION));
    summary.insert(QStringLiteral("git_hash"), QStringLiteral(MCU_STUDIO_GIT_HASH));
    summary.insert(QStringLiteral("log_files"), QJsonArray::fromStringList(Logger::recentLogFiles(3)));
    summary.insert(QStringLiteral("pending_crashes"), m_pendingCrashes.size());

    QFile out(fallbackPath);
    if (!out.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        m_lastError = tr("Failed to write diagnostics summary.");
        emit lastErrorChanged();
        emit bundleExported(false, fallbackPath, m_lastError);
        return;
    }
    out.write(QJsonDocument(summary).toJson(QJsonDocument::Indented));
    out.close();
    m_lastExportPath = fallbackPath;
    m_lastStatus = tr("Engine offline; wrote diagnostics summary to %1").arg(fallbackPath);
    emit lastExportPathChanged();
    emit lastStatusChanged();
    emit bundleExported(true, fallbackPath, QString());
}

void DiagnosticsService::seedDemoCrashReport() {
    if (!m_pendingCrashes.isEmpty()) {
        return;
    }
    QVariantMap item;
    item.insert(QStringLiteral("id"), QStringLiteral("crash-demo-1732606700-4210"));
    item.insert(QStringLiteral("timestamp"), QStringLiteral("2026-11-26 07:38:20"));
    item.insert(QStringLiteral("signal"), QStringLiteral("SIGSEGV"));
    item.insert(QStringLiteral("version"), QStringLiteral(MCU_STUDIO_VERSION));
    item.insert(QStringLiteral("gitHash"), QStringLiteral(MCU_STUDIO_GIT_HASH));
    item.insert(QStringLiteral("platform"), QSysInfo::kernelType());
    item.insert(QStringLiteral("filePath"), QStringLiteral("<demo-memory>"));
    item.insert(QStringLiteral("synthetic"), true);
    item.insert(QStringLiteral("backtrace"),
                QStringLiteral("#0 0x00007f9a12345678 in Application::triggerCrashTest()+0x14\n"
                               "#1 0x00007f9a12340110 in QSingleShotTimer::timerEvent()+0x42\n"
                               "#2 0x00007f9a0e41b890 in QObject::event(QEvent*)+0xb0"));
    m_pendingCrashes.append(item);
    emit crashReportsChanged();
}
