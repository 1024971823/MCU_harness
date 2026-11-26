#include "app/AppInfo.h"
#include "controllers/DemoSeeder.h"
#include "controllers/WorkbenchController.h"
#include "core/AppSettings.h"
#include "core/DiagnosticsService.h"
#include "core/IconProvider.h"
#include "core/LicenseManager.h"
#include "core/UpdateManager.h"
#include "core/Utils.h"

#include <QDir>
#include <QFile>
#include <QMetaObject>
#include <QQmlApplicationEngine>
#include <QQuickStyle>
#include <QQuickWindow>
#include <QStringList>
#include <QTest>
#include <QUrl>

namespace {

QStringList g_qmlWarnings;

void qmlSmokeMsgHandler(QtMsgType type, const QMessageLogContext &ctx, const QString &msg) {
    Q_UNUSED(ctx);
    if (type == QtWarningMsg || type == QtCriticalMsg || type == QtFatalMsg) {
        if (msg.contains(QStringLiteral(".qml:")) || msg.contains(QStringLiteral("ReferenceError")) ||
            msg.contains(QStringLiteral("TypeError")) || msg.contains(QStringLiteral("Unable to assign")) ||
            msg.contains(QStringLiteral("Cannot assign")) || msg.contains(QStringLiteral("is not defined"))) {
            g_qmlWarnings.append(msg);
        }
    }
}

} // namespace

class TestQmlSmoke : public QObject {
    Q_OBJECT

private slots:
    void initTestCase();
    void cleanupTestCase();
    void testLoadMainAndCycleAllStates();
};

void TestQmlSmoke::initTestCase() {
    qputenv("QT_QPA_PLATFORM", QByteArrayLiteral("offscreen"));
    qputenv("QT_QUICK_BACKEND", QByteArrayLiteral("software"));
    QQuickStyle::setStyle(QStringLiteral("Basic"));
    g_qmlWarnings.clear();
    qInstallMessageHandler(qmlSmokeMsgHandler);
}

void TestQmlSmoke::cleanupTestCase() {
    qInstallMessageHandler(nullptr);
}

void TestQmlSmoke::testLoadMainAndCycleAllStates() {
    AppSettings settings(true);
    AppInfo appInfo(true, true);
    LicenseManager license;
    UpdateManager updater;
    DiagnosticsService diagnostics;
    Utils utils;
    WorkbenchController workbench(&settings, &appInfo, &updater, &diagnostics, true);
    DemoSeeder::seed(&workbench, &updater, &diagnostics);

    qmlRegisterSingletonInstance("McuStudio", 1, 0, "AppInfo", &appInfo);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "Settings", &settings);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "Workbench", &workbench);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "License", &license);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "Updater", &updater);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "Diagnostics", &diagnostics);
    qmlRegisterSingletonInstance("McuStudio", 1, 0, "Utils", &utils);

    QQmlApplicationEngine engine;
    engine.addImageProvider(QStringLiteral("icon"), new IconProvider());

    const QUrl mainUrl(QStringLiteral("qrc:/qt/qml/McuStudio/qml/Main.qml"));
    engine.load(mainUrl);

    QVERIFY2(!engine.rootObjects().isEmpty(), qPrintable(g_qmlWarnings.join(QLatin1Char('\n'))));

    QObject *root = engine.rootObjects().first();
    QQuickWindow *win = qobject_cast<QQuickWindow *>(root);
    QVERIFY(win != nullptr);
    win->resize(1440, 900);
    win->show();
    QTest::qWait(50);

    QObject *driverObj = root->findChild<QObject *>(QStringLiteral("screenshotDriver"));
    QVERIFY2(driverObj != nullptr, "screenshotDriver not found in Main.qml");

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

    for (const QString &st : states) {
        QMetaObject::invokeMethod(driverObj, "gotoState", Q_ARG(QVariant, st));
        QTest::qWait(20);
    }

    // Exercise dark & light toggle
    settings.setTheme(QStringLiteral("light"));
    QTest::qWait(20);
    settings.setTheme(QStringLiteral("dark"));
    QTest::qWait(20);

    QVERIFY2(g_qmlWarnings.isEmpty(), qPrintable(QStringLiteral("QML warnings:\n") + g_qmlWarnings.join(QLatin1Char('\n'))));
}

QTEST_MAIN(TestQmlSmoke)
#include "tst_qml_smoke.moc"
