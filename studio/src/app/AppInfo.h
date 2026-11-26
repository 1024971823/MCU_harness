#pragma once

#include <QObject>
#include <QString>

class AppInfo : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString version READ version CONSTANT)
    Q_PROPERTY(QString engineVersion READ engineVersion NOTIFY engineVersionChanged)
    Q_PROPERTY(QString gitHash READ gitHash CONSTANT)
    Q_PROPERTY(QString buildDate READ buildDate CONSTANT)
    Q_PROPERTY(QString qtVersion READ qtVersion CONSTANT)
    Q_PROPERTY(QString osName READ osName CONSTANT)
    Q_PROPERTY(QString appName READ appName CONSTANT)
    Q_PROPERTY(QString organization READ organization CONSTANT)
    Q_PROPERTY(QString domain READ domain CONSTANT)
    Q_PROPERTY(QString machineId READ machineId CONSTANT)
    Q_PROPERTY(bool demoMode READ demoMode CONSTANT)
    Q_PROPERTY(bool screenshotMode READ screenshotMode CONSTANT)
    Q_PROPERTY(QString thirdPartyLicenses READ thirdPartyLicenses CONSTANT)

public:
    explicit AppInfo(bool demoMode = false, bool screenshotMode = false, QObject *parent = nullptr);

    QString version() const;
    QString engineVersion() const { return m_engineVersion; }
    void setEngineVersion(const QString &v);
    QString gitHash() const;
    QString buildDate() const;
    QString qtVersion() const;
    QString osName() const;
    QString appName() const;
    QString organization() const;
    QString domain() const;
    QString machineId() const;
    bool demoMode() const;
    bool screenshotMode() const;
    QString thirdPartyLicenses() const;

signals:
    void engineVersionChanged();

private:
    bool m_demoMode = false;
    bool m_screenshotMode = false;
    QString m_machineId;
    QString m_engineVersion = QStringLiteral("0.5.0");
};
