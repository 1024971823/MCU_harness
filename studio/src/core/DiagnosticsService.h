#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QString>
#include <QUrl>
#include <QVariantList>

class EngineClient;

class DiagnosticsService : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList pendingCrashReports READ pendingCrashReports NOTIFY crashReportsChanged)
    Q_PROPERTY(bool hasPendingCrashes READ hasPendingCrashes NOTIFY crashReportsChanged)
    Q_PROPERTY(bool hasCrashReport READ hasPendingCrashes NOTIFY crashReportsChanged)
    Q_PROPERTY(QString logDirectory READ logDirectory CONSTANT)
    Q_PROPERTY(QString crashDirectory READ crashDirectory CONSTANT)
    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    Q_PROPERTY(QString lastExportPath READ lastExportPath NOTIFY lastExportPathChanged)
    Q_PROPERTY(QString lastStatus READ lastStatus NOTIFY lastStatusChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    Q_PROPERTY(QString crashTime READ crashTime NOTIFY crashReportsChanged)
    Q_PROPERTY(QString crashSignal READ crashSignal NOTIFY crashReportsChanged)
    Q_PROPERTY(QString crashBacktrace READ crashBacktrace NOTIFY crashReportsChanged)

public:
    explicit DiagnosticsService(QObject *parent = nullptr);

    void setEngineClient(EngineClient *client) { m_engineClient = client; }

    QVariantList pendingCrashReports() const { return m_pendingCrashes; }
    bool hasPendingCrashes() const { return !m_pendingCrashes.isEmpty(); }
    QString logDirectory() const;
    QString crashDirectory() const;
    bool busy() const { return m_busy; }
    QString lastExportPath() const { return m_lastExportPath; }
    QString lastStatus() const { return m_lastStatus; }
    QString lastError() const { return m_lastError; }
    QString crashTime() const;
    QString crashSignal() const;
    QString crashBacktrace() const;

    Q_INVOKABLE void scanCrashReports();
    Q_INVOKABLE void dismissCrash(const QString &id);
    Q_INVOKABLE void dismissCrashReport();
    Q_INVOKABLE void uploadCrash(const QString &id);
    Q_INVOKABLE void sendCrashReport();
    Q_INVOKABLE void openLogFolder() const;
    Q_INVOKABLE void exportBundle(const QUrl &destUrl);
    Q_INVOKABLE void seedDemoCrashReport();

signals:
    void crashReportsChanged();
    void busyChanged();
    void lastExportPathChanged();
    void lastStatusChanged();
    void lastErrorChanged();
    void bundleExported(bool ok, const QString &path, const QString &error);

private:
    void markCrashAcked(const QString &id);
    void setBusy(bool b);

    EngineClient *m_engineClient = nullptr;
    QNetworkAccessManager m_nam;
    QVariantList m_pendingCrashes;
    bool m_busy = false;
    QString m_lastExportPath;
    QString m_lastStatus;
    QString m_lastError;
};
