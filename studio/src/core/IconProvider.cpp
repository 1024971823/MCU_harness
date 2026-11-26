#include "core/IconProvider.h"
#include "BuildConfig.h"

#include <QColor>
#include <QFile>
#include <QMutexLocker>
#include <QPainter>
#include <QSvgRenderer>
#include <QUrl>
#include <QUrlQuery>

IconProvider::IconProvider()
    : QQuickImageProvider(QQuickImageProvider::Image) {}

QByteArray IconProvider::loadSvgBytes(const QString &name) {
    {
        QMutexLocker lock(&m_mutex);
        auto it = m_svgCache.constFind(name);
        if (it != m_svgCache.constEnd()) {
            return it.value();
        }
    }

    const QStringList candidates = {
        QStringLiteral(":/qt/qml/McuStudio/resources/icons/%1.svg").arg(name),
        QStringLiteral(":/icons/%1.svg").arg(name),
        QStringLiteral(MCU_STUDIO_SOURCE_DIR "/resources/icons/%1.svg").arg(name),
    };

    QByteArray data;
    for (const QString &path : candidates) {
        QFile f(path);
        if (f.open(QIODevice::ReadOnly)) {
            data = f.readAll();
            break;
        }
    }

    if (!data.isEmpty()) {
        QMutexLocker lock(&m_mutex);
        m_svgCache.insert(name, data);
    }
    return data;
}

QImage IconProvider::requestImage(const QString &id, QSize *size, const QSize &requestedSize) {
    QString iconName = id;
    QString colorStr;
    int pxSize = 24;

    const int qIdx = id.indexOf(QLatin1Char('?'));
    if (qIdx >= 0) {
        iconName = id.left(qIdx);
        QUrlQuery query(id.mid(qIdx + 1));
        if (query.hasQueryItem(QStringLiteral("color"))) {
            colorStr = query.queryItemValue(QStringLiteral("color"), QUrl::FullyDecoded);
        }
        if (query.hasQueryItem(QStringLiteral("size"))) {
            bool ok = false;
            int s = query.queryItemValue(QStringLiteral("size")).toInt(&ok);
            if (ok && s > 0) {
                pxSize = qBound(8, s, 512);
            }
        }
    }

    if (iconName.endsWith(QStringLiteral(".svg"))) {
        iconName.chop(4);
    }

    int targetW = (requestedSize.width() > 0) ? requestedSize.width() : pxSize;
    int targetH = (requestedSize.height() > 0) ? requestedSize.height() : pxSize;
    targetW = qBound(8, targetW, 512);
    targetH = qBound(8, targetH, 512);

    if (size) {
        *size = QSize(targetW, targetH);
    }

    QByteArray svgBytes = loadSvgBytes(iconName);
    if (svgBytes.isEmpty()) {
        QImage empty(targetW, targetH, QImage::Format_ARGB32_Premultiplied);
        empty.fill(Qt::transparent);
        return empty;
    }

    QColor tint;
    if (!colorStr.isEmpty()) {
        if (!colorStr.startsWith(QLatin1Char('#')) && colorStr.size() >= 6 && colorStr.size() <= 8) {
            QColor test(QLatin1Char('#') + colorStr);
            if (test.isValid()) {
                tint = test;
            }
        }
        if (!tint.isValid()) {
            tint = QColor(colorStr);
        }
    }

    const bool replacedCurrentColor = tint.isValid() && svgBytes.contains("currentColor");
    if (replacedCurrentColor) {
        svgBytes.replace("currentColor", tint.name(QColor::HexRgb).toUtf8());
    }

    QSvgRenderer renderer(svgBytes);
    if (!renderer.isValid()) {
        QImage empty(targetW, targetH, QImage::Format_ARGB32_Premultiplied);
        empty.fill(Qt::transparent);
        return empty;
    }

    QImage img(targetW, targetH, QImage::Format_ARGB32_Premultiplied);
    img.fill(Qt::transparent);

    QPainter painter(&img);
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setRenderHint(QPainter::SmoothPixmapTransform, true);
    renderer.render(&painter, QRectF(0, 0, targetW, targetH));

    if (tint.isValid() && !replacedCurrentColor) {
        painter.setCompositionMode(QPainter::CompositionMode_SourceIn);
        painter.fillRect(img.rect(), tint);
    } else if (tint.isValid() && tint.alpha() < 255) {
        painter.setCompositionMode(QPainter::CompositionMode_DestinationIn);
        painter.fillRect(img.rect(), QColor(0, 0, 0, tint.alpha()));
    }
    painter.end();

    return img;
}
