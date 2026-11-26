#pragma once

#include <QByteArray>
#include <QNetworkAccessManager>
#include <QObject>
#include <QString>

class QNetworkReply;

class UpdateManager : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool checking READ checking NOTIFY stateChanged)
    Q_PROPERTY(bool downloading READ downloading NOTIFY stateChanged)
    Q_PROPERTY(bool updateAvailable READ updateAvailable NOTIFY stateChanged)
    Q_PROPERTY(QString status READ status NOTIFY stateChanged)
    Q_PROPERTY(QString currentVersion READ currentVersion CONSTANT)
    Q_PROPERTY(QString latestVersion READ latestVersion NOTIFY stateChanged)
    Q_PROPERTY(QString releaseNotes READ releaseNotes NOTIFY stateChanged)
    Q_PROPERTY(QString downloadUrl READ downloadUrl NOTIFY stateChanged)
    Q_PROPERTY(double downloadProgress READ downloadProgress NOTIFY downloadProgressChanged)
    Q_PROPERTY(QString downloadedFilePath READ downloadedFilePath NOTIFY stateChanged)
    Q_PROPERTY(QString statusMessage READ statusMessage NOTIFY stateChanged)
    Q_PROPERTY(QString errorMessage READ errorMessage NOTIFY stateChanged)
    Q_PROPERTY(QString manifestUrl READ manifestUrl WRITE setManifestUrl NOTIFY manifestUrlChanged)
    Q_PROPERTY(QString publishedAt READ publishedAt NOTIFY stateChanged)
    Q_PROPERTY(bool isMandatory READ isMandatory NOTIFY stateChanged)
    Q_PROPERTY(QString expectedSha256 READ expectedSha256 NOTIFY stateChanged)
    Q_PROPERTY(qint64 expectedSize READ expectedSize NOTIFY stateChanged)

public:
    explicit UpdateManager(QObject *parent = nullptr);
    UpdateManager(const QString &pubkeyB64,
                  const QString &currentVersion,
                  const QString &downloadDir = QString(),
                  QObject *parent = nullptr);

    bool checking() const { return m_checking; }
    bool downloading() const { return m_downloading; }
    bool updateAvailable() const { return m_updateAvailable; }
    QString status() const;
    QString currentVersion() const { return m_currentVersion; }
    QString latestVersion() const { return m_latestVersion; }
    QString releaseNotes() const { return m_releaseNotes; }
    QString downloadUrl() const { return m_downloadUrl; }
    double downloadProgress() const { return m_downloadProgress; }
    QString downloadedFilePath() const { return m_downloadedFilePath; }
    QString statusMessage() const { return m_statusMessage; }
    QString errorMessage() const { return m_errorMessage; }
    QString manifestUrl() const { return m_manifestUrl; }
    void setManifestUrl(const QString &url);
    QString publishedAt() const { return m_publishedAt; }
    bool isMandatory() const { return m_isMandatory; }
    QString expectedSha256() const { return m_expectedSha256; }
    qint64 expectedSize() const { return m_expectedSize; }

    Q_INVOKABLE void checkForUpdates(const QString &manifestUrlOverride = QString());
    Q_INVOKABLE void downloadUpdate();
    Q_INVOKABLE void installUpdate();
    Q_INVOKABLE void installAndRestart() { installUpdate(); }
    Q_INVOKABLE void dismissUpdate();
    Q_INVOKABLE void skipVersion(const QString &version);
    Q_INVOKABLE void setDemoAvailableState();

    void setLanguageHint(const QString &lang) { m_language = lang; }

    static int compareSemver(const QString &a, const QString &b);
    bool verifyManifestSignature(const QByteArray &manifestBytes, const QByteArray &sigBytesOrB64) const;
    static bool verifyFileSha256AndSize(const QString &filePath,
                                        const QString &expectedSha256Hex,
                                        qint64 expectedSize);

signals:
    void stateChanged();
    void downloadProgressChanged();
    void manifestUrlChanged();
    void updateCheckFinished(bool available, const QString &error);
    void downloadFinished(bool ok, const QString &filePath, const QString &error);

private:
    void handleManifestAndSigFetched(const QByteArray &manifestBytes, const QByteArray &sigBytes);
    static QString currentPlatformAssetKey();
    static QString defaultDownloadDir();

    QNetworkAccessManager m_nam;
    QString m_pubkeyB64;
    QString m_currentVersion;
    QString m_downloadDir;
    QString m_language = QStringLiteral("en");
    QString m_manifestUrl;

    bool m_checking = false;
    bool m_downloading = false;
    bool m_updateAvailable = false;
    QString m_latestVersion;
    QString m_releaseNotes;
    QString m_downloadUrl;
    double m_downloadProgress = 0.0;
    QString m_downloadedFilePath;
    QString m_statusMessage;
    QString m_errorMessage;
    QString m_publishedAt;
    bool m_isMandatory = false;
    QString m_expectedSha256;
    qint64 m_expectedSize = 0;

    QNetworkReply *m_activeDownloadReply = nullptr;
};
