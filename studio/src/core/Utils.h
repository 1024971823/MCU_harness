#pragma once

#include <QObject>
#include <QString>
#include <QUrl>

class Utils : public QObject {
    Q_OBJECT

public:
    explicit Utils(QObject *parent = nullptr);

    Q_INVOKABLE void copyToClipboard(const QString &text) const;
    Q_INVOKABLE QString formatBytes(qint64 bytes) const;
    Q_INVOKABLE QString formatTimestamp(double epochSeconds) const;
    Q_INVOKABLE QString formatDurationMs(qint64 ms) const;
    Q_INVOKABLE QString basename(const QString &path) const;
    Q_INVOKABLE bool fileExists(const QString &path) const;
    Q_INVOKABLE void openExternalUrl(const QUrl &url) const;
    Q_INVOKABLE void openPathInFileManager(const QString &path) const;
    Q_INVOKABLE QString urlToLocalFile(const QUrl &url) const;
};
