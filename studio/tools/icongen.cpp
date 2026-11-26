#include <QByteArray>
#include <QCommandLineParser>
#include <QCoreApplication>
#include <QDataStream>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QGuiApplication>
#include <QImage>
#include <QPainter>
#include <QSvgRenderer>

#include <cstdio>

namespace {

bool renderSvgToPng(QSvgRenderer &renderer, int size, const QString &outPath, QByteArray *pngBytes = nullptr) {
    QImage img(size, size, QImage::Format_ARGB32_Premultiplied);
    img.fill(Qt::transparent);
    QPainter painter(&img);
    painter.setRenderHint(QPainter::Antialiasing, true);
    painter.setRenderHint(QPainter::SmoothPixmapTransform, true);
    renderer.render(&painter, QRectF(0, 0, size, size));
    painter.end();

    if (pngBytes) {
        pngBytes->clear();
        QDataStream stream(pngBytes, QIODevice::WriteOnly);
        Q_UNUSED(stream);
        if (!img.save(outPath, "PNG")) {
            return false;
        }
        QFile f(outPath);
        if (!f.open(QIODevice::ReadOnly)) {
            return false;
        }
        *pngBytes = f.readAll();
        return true;
    }
    return img.save(outPath, "PNG");
}

bool writeIcoFile(const QList<QPair<int, QByteArray>> &entries, const QString &icoPath) {
    QFile out(icoPath);
    if (!out.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
        return false;
    }
    QDataStream ds(&out);
    ds.setByteOrder(QDataStream::LittleEndian);

    // ICONDIR
    ds << quint16(0) << quint16(1) << quint16(entries.size());

    quint32 offset = 6u + static_cast<quint32>(entries.size()) * 16u;
    for (const auto &entry : entries) {
        const int sz = entry.first;
        const QByteArray &data = entry.second;
        const quint8 dim = (sz >= 256) ? 0 : static_cast<quint8>(sz);
        ds << dim << dim << quint8(0) << quint8(0);
        ds << quint16(1) << quint16(32);
        ds << static_cast<quint32>(data.size());
        ds << offset;
        offset += static_cast<quint32>(data.size());
    }
    for (const auto &entry : entries) {
        if (out.write(entry.second) != entry.second.size()) {
            return false;
        }
    }
    return true;
}

} // namespace

int main(int argc, char *argv[]) {
    qputenv("QT_QPA_PLATFORM", QByteArrayLiteral("offscreen"));
    QGuiApplication app(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("studio-icongen"));

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Render SVG app icon to PNGs and Windows ICO."));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("input_svg"), QStringLiteral("Source SVG file"));
    parser.addPositionalArgument(QStringLiteral("output_dir"), QStringLiteral("Output directory"));
    parser.process(app);

    const QStringList args = parser.positionalArguments();
    if (args.size() < 2) {
        std::fprintf(stderr, "Usage: studio-icongen <input_svg> <output_dir>\n");
        return 1;
    }

    const QString svgPath = args.at(0);
    const QString outDirPath = args.at(1);

    QSvgRenderer renderer(svgPath);
    if (!renderer.isValid()) {
        std::fprintf(stderr, "Invalid SVG: %s\n", qPrintable(svgPath));
        return 1;
    }

    QDir outDir(outDirPath);
    if (!outDir.exists() && !outDir.mkpath(QStringLiteral("."))) {
        std::fprintf(stderr, "Failed to create output directory: %s\n", qPrintable(outDirPath));
        return 1;
    }

    const QList<int> sizes{16, 24, 32, 48, 64, 128, 256, 512};
    QList<QPair<int, QByteArray>> icoEntries;
    for (int sz : sizes) {
        const QString pngPath = outDir.filePath(QStringLiteral("mcu-harness-studio-%1.png").arg(sz));
        QByteArray pngBytes;
        if (!renderSvgToPng(renderer, sz, pngPath, &pngBytes)) {
            std::fprintf(stderr, "Failed to render %dx%d PNG\n", sz, sz);
            return 1;
        }
        if (sz <= 256) {
            icoEntries.append({sz, pngBytes});
        }
    }

    const QString icoPath = outDir.filePath(QStringLiteral("mcu-harness-studio.ico"));
    if (!writeIcoFile(icoEntries, icoPath)) {
        std::fprintf(stderr, "Failed to write ICO: %s\n", qPrintable(icoPath));
        return 1;
    }

    return 0;
}
