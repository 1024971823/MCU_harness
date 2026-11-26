#include "app/Application.h"
#include "BuildConfig.h"
#include "app/AppInfo.h"
#include "controllers/DemoSeeder.h"
#include "controllers/WorkbenchController.h"
#include "core/AppSettings.h"
#include "core/CrashHandler.h"
#include "core/DiagnosticsService.h"
#include "core/IconProvider.h"
#include "core/LicenseManager.h"
#include "core/Logger.h"
#include "core/UpdateManager.h"
#include "core/Utils.h"

#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFont>
#include <QFontDatabase>
#include <QGuiApplication>
#include <QIcon>
#include <QMetaObject>
#include <QQmlApplicationEngine>
#include <QQmlContext>
#include <QQuickStyle>
#include <QQuickWindow>
#include <QSettings>
#include <QTimer>
#include <QTranslator>
#include <QUrl>

#include <cstdio>
#include <cstring>
#include <functional>
#include <memory>

namespace {

void printCliHelp() {
    std::printf(
        "MCU Harness Studio %s (%s)\n"
        "Usage: mcu-harness-studio [options]\n\n"
        "Options:\n"
        "  -h, --help                Show this help and exit\n"
        "  -v, --version             Show version and exit\n"
        "  --project-dir <path>      Target embedded project root directory\n"
        "  --provider <name>         LLM provider (openai/deepseek/qwen/openrouter/ollama/vllm/anthropic/gemini/mock)\n"
        "  --model <name>            Model identifier\n"
        "  --base-url <url>          Custom API base URL\n"
        "  --api-key <key>           API key override\n"
        "  --mode <mode>             Interlock mode (interactive|auto_safe|read_only)\n"
        "  --mock-llm                Use built-in offline MockLlmProvider\n"
        "  --lang <en|zh_CN>         UI language override\n"
        "  --theme <dark|light|system> UI theme override\n"
        "  --reset-settings          Reset persisted settings to defaults\n"
        "  --demo                    Launch in offline demo mode with sample hardware telemetry\n"
        "  --screenshot <dir>        Capture matrix of UI screenshots to <dir> and exit\n"
        "  --crash-test              Trigger intentional crash after startup to test CrashHandler\n",
        MCU_STUDIO_VERSION,
        MCU_STUDIO_GIT_HASH);
}

bool hasFlag(int argc, char *argv[], const char *shortFlag, const char *longFlag) {
    for (int i = 1; i < argc; ++i) {
        if ((shortFlag && std::strcmp(argv[i], shortFlag) == 0) ||
            (longFlag && std::strcmp(argv[i], longFlag) == 0)) {
            return true;
        }
    }
    return false;
}

void loadBundledFonts() {
    const QStringList qrcDirs = {
        QStringLiteral(":/qt/qml/McuStudio/resources/fonts"),
        QStringLiteral(MCU_STUDIO_SOURCE_DIR "/resources/fonts"),
    };
    for (const QString &dirPath : qrcDirs) {
        QDir dir(dirPath);
        if (!dir.exists()) {
            continue;
        }
        const QStringList ttfs = dir.entryList(QStringList{QStringLiteral("*.ttf")}, QDir::Files);
        for (const QString &f : ttfs) {
            QFontDatabase::addApplicationFont(dir.filePath(f));
        }
        if (!ttfs.isEmpty()) {
            break;
        }
    }
}

void applyTranslator(QCoreApplication &app, QQmlApplicationEngine &engine, QTranslator &translator, const QString &lang) {
    app.removeTranslator(&translator);
    if (lang.startsWith(QStringLiteral("zh"), Qt::CaseInsensitive)) {
        const QStringList candidates = {
            QStringLiteral(":/i18n/mcu_studio_zh_CN.qm"),
            QStringLiteral(":/i18n/McuHarnessStudio_zh_CN.qm"),
        };
        for (const QString &qmPath : candidates) {
            if (QFile::exists(qmPath) && translator.load(qmPath)) {
                app.installTranslator(&translator);
                break;
            }
        }
    }
    engine.retranslate();
}

void runScreenshotMatrix(QQmlApplicationEngine &engine,
                         QQuickWindow *win,
                         AppSettings &settings,
                         WorkbenchController &workbench,
                         const QString &outDirPath) {
    QObject *driverObj = nullptr;
    for (QObject *rootObj : engine.rootObjects()) {
        driverObj = rootObj->findChild<QObject *>(QStringLiteral("screenshotDriver"));
        if (driverObj) {
            break;
        }
    }
    if (!driverObj) {
        qWarning("ScreenshotDriver not found in QML tree; aborting screenshot capture.");
        QCoreApplication::exit(1);
        return;
    }

    QDir outDir(outDirPath);
    if (!outDir.exists()) {
        outDir.mkpath(QStringLiteral("."));
    }

    win->resize(1440, 900);
    win->show();

    struct ShotJob {
        QString lang;
        QString theme;
        QString state;
    };
    const QStringList langs = {QStringLiteral("zh_CN"), QStringLiteral("en")};
    const QStringList themes = {QStringLiteral("dark"), QStringLiteral("light")};
    const QStringList states = {
        QStringLiteral("agent-empty"),
        QStringLiteral("agent-conversation"),
        QStringLiteral("approval-danger"),
        QStringLiteral("approval-diff"),
        QStringLiteral("hardware"),
        QStringLiteral("watch"),
        QStringLiteral("rtos"),
        QStringLiteral("sessions"),
        QStringLiteral("audit"),
        QStringLiteral("settings-model"),
        QStringLiteral("settings-appearance"),
        QStringLiteral("license"),
        QStringLiteral("update-available"),
        QStringLiteral("onboarding-1"),
        QStringLiteral("onboarding-3"),
        QStringLiteral("palette"),
        QStringLiteral("crash"),
    };

    auto jobs = std::make_shared<QList<ShotJob>>();
    for (const QString &l : langs) {
        for (const QString &t : themes) {
            for (const QString &s : states) {
                jobs->append({l, t, s});
            }
        }
    }

    auto indexPtr = std::make_shared<int>(0);
    auto stepFn = std::make_shared<std::function<void()>>();

    *stepFn = [win, &settings, &workbench, driverObj, outDir, jobs, indexPtr, stepFn]() {
        if (*indexPtr >= jobs->size()) {
            QCoreApplication::quit();
            return;
        }

        const ShotJob job = jobs->at(*indexPtr);
        settings.setLanguage(job.lang);
        settings.setTheme(job.theme);
        Q_UNUSED(workbench);
        QMetaObject::invokeMethod(driverObj, "gotoState", Q_ARG(QVariant, job.state));

        // Wait 2 rendered frames or settle timer to capture cleanly
        auto framesLeft = std::make_shared<int>(2);
        auto captured = std::make_shared<bool>(false);
        auto conn = std::make_shared<QMetaObject::Connection>();

        auto doCapture = [win, outDir, job, indexPtr, stepFn, captured, conn]() {
            if (*captured) {
                return;
            }
            *captured = true;
            QObject::disconnect(*conn);

            const QImage img = win->grabWindow();
            const QString fileName = QStringLiteral("%1-%2-%3.png").arg(job.lang, job.theme, job.state);
            img.save(outDir.filePath(fileName), "PNG");
            ++(*indexPtr);
            QTimer::singleShot(15, win, *stepFn);
        };

        *conn = QObject::connect(win, &QQuickWindow::frameSwapped, win, [win, framesLeft, doCapture]() {
            --(*framesLeft);
            if (*framesLeft <= 0) {
                QTimer::singleShot(260, win, doCapture);
            }
        });
        // Fallback if offscreen platform does not emit frameSwapped continuously
        QTimer::singleShot(280, win, doCapture);
        win->requestUpdate();
    };

    QTimer::singleShot(120, win, *stepFn);
}

} // namespace

int Application::run(int argc, char *argv[]) {
    // Handle --help / --version before constructing QGuiApplication so CLI works headless without display
    if (hasFlag(argc, argv, "-h", "--help")) {
        printCliHelp();
        return 0;
    }
    if (hasFlag(argc, argv, "-v", "--version")) {
        std::printf("MCU Harness Studio %s (%s)\n", MCU_STUDIO_VERSION, MCU_STUDIO_GIT_HASH);
        return 0;
    }

    QCoreApplication::setOrganizationName(QStringLiteral("Lannuo"));
    QCoreApplication::setOrganizationDomain(QStringLiteral("mcuharness.dev"));
    QCoreApplication::setApplicationName(QStringLiteral("MCU Harness Studio"));
    QCoreApplication::setApplicationVersion(QStringLiteral(MCU_STUDIO_VERSION));

    QQuickStyle::setStyle(QStringLiteral("Basic"));
    QGuiApplication::setHighDpiScaleFactorRoundingPolicy(Qt::HighDpiScaleFactorRoundingPolicy::PassThrough);

    QGuiApplication app(argc, argv);

    QCommandLineParser parser;
    parser.addOption(QCommandLineOption(QStringLiteral("project-dir"), QStringLiteral("Project directory"), QStringLiteral("dir")));
    parser.addOption(QCommandLineOption(QStringLiteral("provider"), QStringLiteral("LLM provider"), QStringLiteral("name")));
    parser.addOption(QCommandLineOption(QStringLiteral("model"), QStringLiteral("Model name"), QStringLiteral("name")));
    parser.addOption(QCommandLineOption(QStringLiteral("base-url"), QStringLiteral("Base URL"), QStringLiteral("url")));
    parser.addOption(QCommandLineOption(QStringLiteral("api-key"), QStringLiteral("API key"), QStringLiteral("key")));
    parser.addOption(QCommandLineOption(QStringLiteral("mode"), QStringLiteral("Interlock mode"), QStringLiteral("mode")));
    parser.addOption(QCommandLineOption(QStringLiteral("mock-llm"), QStringLiteral("Use mock LLM")));
    parser.addOption(QCommandLineOption(QStringLiteral("lang"), QStringLiteral("Language"), QStringLiteral("lang")));
    parser.addOption(QCommandLineOption(QStringLiteral("theme"), QStringLiteral("Theme"), QStringLiteral("theme")));
    parser.addOption(QCommandLineOption(QStringLiteral("reset-settings"), QStringLiteral("Reset settings")));
    parser.addOption(QCommandLineOption(QStringLiteral("screenshot"), QStringLiteral("Capture screenshots"), QStringLiteral("dir")));
    parser.addOption(QCommandLineOption(QStringLiteral("crash-test"), QStringLiteral("Trigger crash test")));
    parser.addOption(QCommandLineOption(QStringLiteral("demo"), QStringLiteral("Run in demo mode")));
    parser.parse(app.arguments());

    const QString screenshotDir = parser.value(QStringLiteral("screenshot")).trimmed();
    const bool screenshotMode = !screenshotDir.isEmpty();
    const bool demoMode = parser.isSet(QStringLiteral("demo")) || screenshotMode;
    const bool crashTest = parser.isSet(QStringLiteral("crash-test"));

    Logger::install();
    CrashHandler::install(QStringLiteral(MCU_STUDIO_VERSION), QStringLiteral(MCU_STUDIO_GIT_HASH));

    loadBundledFonts();

    AppSettings settings(screenshotMode);
    if (parser.isSet(QStringLiteral("reset-settings"))) {
        settings.resetToDefaults();
    }
    if (parser.isSet(QStringLiteral("project-dir"))) {
        settings.setProjectDir(parser.value(QStringLiteral("project-dir")));
    }
    if (parser.isSet(QStringLiteral("provider"))) {
        settings.setProvider(parser.value(QStringLiteral("provider")));
    }
    if (parser.isSet(QStringLiteral("model"))) {
        settings.setModel(parser.value(QStringLiteral("model")));
    }
    if (parser.isSet(QStringLiteral("base-url"))) {
        settings.setBaseUrl(parser.value(QStringLiteral("base-url")));
    }
    if (parser.isSet(QStringLiteral("api-key"))) {
        settings.setApiKey(parser.value(QStringLiteral("api-key")));
    }
    if (parser.isSet(QStringLiteral("mode"))) {
        settings.setInterlockMode(parser.value(QStringLiteral("mode")));
    }
    if (parser.isSet(QStringLiteral("mock-llm"))) {
        settings.setMockLlm(true);
    }
    if (parser.isSet(QStringLiteral("lang"))) {
        settings.setLanguage(parser.value(QStringLiteral("lang")));
    }
    if (parser.isSet(QStringLiteral("theme"))) {
        settings.setTheme(parser.value(QStringLiteral("theme")));
    }

    QFont appFont;
    appFont.setFamilies({
        QStringLiteral("Inter"),
        QStringLiteral("Noto Sans CJK SC"),
        QStringLiteral("Microsoft YaHei UI"),
        QStringLiteral("PingFang SC"),
        QStringLiteral("Noto Sans"),
    });
    appFont.setPixelSize(settings.fontSize());
    app.setFont(appFont);

    if (QFile::exists(QStringLiteral(":/qt/qml/McuStudio/resources/app/mcu-harness-studio.svg"))) {
        app.setWindowIcon(QIcon(QStringLiteral(":/qt/qml/McuStudio/resources/app/mcu-harness-studio.svg")));
    } else if (QFile::exists(QStringLiteral(MCU_STUDIO_SOURCE_DIR "/resources/app/mcu-harness-studio.svg"))) {
        app.setWindowIcon(QIcon(QStringLiteral(MCU_STUDIO_SOURCE_DIR "/resources/app/mcu-harness-studio.svg")));
    }

    AppInfo appInfo(demoMode, screenshotMode);
    LicenseManager licenseManager;
    UpdateManager updateManager;
    updateManager.setManifestUrl(settings.updateUrl());
    updateManager.setLanguageHint(settings.language());
    DiagnosticsService diagnosticsService;
    Utils utils;
    WorkbenchController workbench(&settings, &appInfo, &updateManager, &diagnosticsService, demoMode);

    if (demoMode) {
        DemoSeeder::seed(&workbench, &updateManager, &diagnosticsService);
    }

    qmlRegisterSingletonInstance("McuStudio", 1, 0, "AppInfo", &appInfo);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "Settings", &settings);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "Workbench", &workbench);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "License", &licenseManager);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "Updater", &updateManager);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "Diagnostics", &diagnosticsService);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "Utils", &utils);

    QQmlApplicationEngine engine;
    engine.addImageProvider(QStringLiteral("icon"), new IconProvider());

    QTranslator translator;
    applyTranslator(app, engine, translator, settings.language());
    QObject::connect(&settings, &AppSettings::languageChanged, &app, [&app, &engine, &translator, &settings, &updateManager]() {
        updateManager.setLanguageHint(settings.language());
        applyTranslator(app, engine, translator, settings.language());
    });

    const QUrl mainQmlUrl = QFile::exists(QStringLiteral(":/qt/qml/McuStudio/qml/Main.qml"))
                                ? QUrl(QStringLiteral("qrc:/qt/qml/McuStudio/qml/Main.qml"))
                                : QUrl::fromLocalFile(QStringLiteral(MCU_STUDIO_SOURCE_DIR "/qml/Main.qml"));

    QObject::connect(
        &engine,
        &QQmlApplicationEngine::objectCreationFailed,
        &app,
        []() { QCoreApplication::exit(-1); },
        Qt::QueuedConnection);

    engine.load(mainQmlUrl);
    if (engine.rootObjects().isEmpty()) {
        return 1;
    }

    QQuickWindow *win = qobject_cast<QQuickWindow *>(engine.rootObjects().first());
    if (win && !screenshotMode) {
        QSettings geomStore(QStringLiteral("Lannuo"), QStringLiteral("MCU Harness Studio"));
        const int w = geomStore.value(QStringLiteral("window/w"), 1440).toInt();
        const int h = geomStore.value(QStringLiteral("window/h"), 900).toInt();
        win->resize(qMax(1100, w), qMax(700, h));
        if (geomStore.contains(QStringLiteral("window/x")) && geomStore.contains(QStringLiteral("window/y"))) {
            win->setPosition(geomStore.value(QStringLiteral("window/x")).toInt(),
                             geomStore.value(QStringLiteral("window/y")).toInt());
        }
        const int vis = geomStore.value(QStringLiteral("window/visibility"), static_cast<int>(QWindow::Windowed)).toInt();
        if (vis == static_cast<int>(QWindow::Maximized)) {
            win->showMaximized();
        }

        QObject::connect(win, &QWindow::windowStateChanged, &workbench, [&workbench](Qt::WindowState st) {
            workbench.setWindowMinimized((st & Qt::WindowMinimized) != 0);
        });
    }

    QObject::connect(&app, &QCoreApplication::aboutToQuit, &app, [&workbench, &settings, win, screenshotMode]() {
        if (win && !screenshotMode) {
            QSettings geomStore(QStringLiteral("Lannuo"), QStringLiteral("MCU Harness Studio"));
            geomStore.setValue(QStringLiteral("window/x"), win->x());
            geomStore.setValue(QStringLiteral("window/y"), win->y());
            geomStore.setValue(QStringLiteral("window/w"), win->width());
            geomStore.setValue(QStringLiteral("window/h"), win->height());
            geomStore.setValue(QStringLiteral("window/visibility"), static_cast<int>(win->visibility()));
            geomStore.sync();
        }
        workbench.shutdownEngine();
        settings.sync();
        Logger::uninstall();
    });

    if (crashTest) {
        QTimer::singleShot(500, &app, []() {
            volatile int *ptr = nullptr;
            *ptr = 42;
        });
    } else if (screenshotMode && win) {
        runScreenshotMatrix(engine, win, settings, workbench, screenshotDir);
    } else if (settings.autoStartEngine()) {
        workbench.startEngine();
        if (!demoMode && settings.autoCheckUpdates() && !settings.updateUrl().isEmpty()) {
            QTimer::singleShot(2500, &updateManager, [&updateManager]() {
                updateManager.checkForUpdates();
            });
        }
    }

    return app.exec();
}
