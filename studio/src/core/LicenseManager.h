#pragma once

#include <QDate>
#include <QJsonObject>
#include <QObject>
#include <QString>
#include <QStringList>
#include <QUrl>

class LicenseManager : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString status READ status NOTIFY licenseChanged)
    Q_PROPERTY(QString tier READ tier NOTIFY licenseChanged)
    Q_PROPERTY(QString edition READ edition NOTIFY licenseChanged)
    Q_PROPERTY(QString holder READ holder NOTIFY licenseChanged)
    Q_PROPERTY(QString licensedTo READ holder NOTIFY licenseChanged)
    Q_PROPERTY(QString email READ email NOTIFY licenseChanged)
    Q_PROPERTY(QString licenseId READ licenseId NOTIFY licenseChanged)
    Q_PROPERTY(int seats READ seats NOTIFY licenseChanged)
    Q_PROPERTY(QString expiresAt READ expiresAt NOTIFY licenseChanged)
    Q_PROPERTY(bool isPerpetual READ isPerpetual NOTIFY licenseChanged)
    Q_PROPERTY(int daysRemaining READ daysRemaining NOTIFY licenseChanged)
    Q_PROPERTY(int trialDaysRemaining READ daysRemaining NOTIFY licenseChanged)
    Q_PROPERTY(int trialDaysTotal READ trialDaysTotal CONSTANT)
    Q_PROPERTY(double trialProgress READ trialProgress NOTIFY licenseChanged)
    Q_PROPERTY(QString machineId READ machineId NOTIFY licenseChanged)
    Q_PROPERTY(bool isValid READ isValid NOTIFY licenseChanged)
    Q_PROPERTY(bool isLicensed READ isLicensed NOTIFY licenseChanged)
    Q_PROPERTY(QStringList features READ features NOTIFY licenseChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY licenseChanged)

public:
    explicit LicenseManager(QObject *parent = nullptr);
    LicenseManager(const QString &pubkeyB64,
                   const QString &storageDir,
                   const QString &machineIdOverride = QString(),
                   QObject *parent = nullptr);

    QString status() const { return m_status; }
    QString tier() const { return m_tier; }
    QString edition() const { return m_tier; }
    QString holder() const { return m_holder; }
    QString email() const { return m_email; }
    QString licenseId() const { return m_licenseId; }
    int seats() const { return m_seats; }
    QString expiresAt() const { return m_expiresAt; }
    bool isPerpetual() const { return m_isPerpetual; }
    int daysRemaining() const { return m_daysRemaining; }
    int trialDaysTotal() const { return 14; }
    double trialProgress() const { return qBound(0.0, static_cast<double>(qMax(0, m_daysRemaining)) / 14.0, 1.0); }
    QString machineId() const { return m_machineId; }
    bool isValid() const { return m_isValid; }
    bool isLicensed() const { return m_isLicensed; }
    QStringList features() const { return m_features; }
    QString lastError() const { return m_lastError; }

    Q_INVOKABLE bool activate(const QString &licenseKey);
    Q_INVOKABLE bool activateKey(const QString &licenseKey) { return activate(licenseKey); }
    Q_INVOKABLE bool activateFromFile(const QUrl &fileUrl);
    Q_INVOKABLE bool importLicenseFile(const QUrl &fileUrl) { return activateFromFile(fileUrl); }
    Q_INVOKABLE void deactivate();
    Q_INVOKABLE void refresh();
    Q_INVOKABLE bool hasFeature(const QString &feature) const;

    bool verifyToken(const QString &token,
                     QJsonObject *outPayload = nullptr,
                     QString *outError = nullptr,
                     const QDate &todayOverride = QDate()) const;

    void setNowEpochOverride(qint64 epochSeconds);
    void setMachineIdOverride(const QString &machineId);
    QString storageDirectory() const { return m_storageDir; }

signals:
    void licenseChanged();

private:
    void evaluateState();
    bool evaluateTrialState();
    void clearLicenseFields();
    qint64 currentEpochSeconds() const;
    QDate currentDate() const;
    QByteArray trialHmac(qint64 startedAt, qint64 lastSeen) const;
    QString licenseFilePath() const;
    QString trialFilePath() const;

    QString m_pubkeyB64;
    QString m_storageDir;
    QString m_machineId;
    qint64 m_nowEpochOverride = -1;

    QString m_status = QStringLiteral("trial");
    QString m_tier = QStringLiteral("trial");
    QString m_holder;
    QString m_email;
    QString m_licenseId;
    int m_seats = 1;
    QString m_expiresAt;
    bool m_isPerpetual = false;
    int m_daysRemaining = 14;
    bool m_isValid = true;
    bool m_isLicensed = false;
    QStringList m_features;
    QString m_lastError;
};
