#pragma once

#include <QByteArray>
#include <QHash>
#include <QImage>
#include <QMutex>
#include <QQuickImageProvider>
#include <QSize>
#include <QString>

class IconProvider : public QQuickImageProvider {
public:
    IconProvider();

    QImage requestImage(const QString &id, QSize *size, const QSize &requestedSize) override;

private:
    QByteArray loadSvgBytes(const QString &name);

    QMutex m_mutex;
    QHash<QString, QByteArray> m_svgCache;
};
