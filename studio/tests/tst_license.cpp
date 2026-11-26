#include "core/LicenseManager.h"

#include <QDir>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTemporaryDir>
#include <QTest>

namespace {

QString readTextFile(const QString &path) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) {
        return QString();
    }
    return QString::fromUtf8(f.readAll()).trimmed();
}

} // namespace

class TestLicense : public QObject {
    Q_OBJECT

private slots:
    void initTestCase();
    void testValidPerpetual();
    void testValidExpiring();
    void testExpired();
    void testMachineBound();
    void testTamperedSignature();
    void testTrialLifecycleAndRollback();
    void testTrialHmacTampering();

private:
    QString m_pubkeyB64;
};

void TestLicense::initTestCase() {
    m_pubkeyB64 = readTextFile(QStringLiteral(MCU_TEST_DATA_DIR "/test_pubkey.b64"));
    QVERIFY2(!m_pubkeyB64.isEmpty(), "Missing test_pubkey.b64 in tests/data");
}

void TestLicense::testValidPerpetual() {
    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());

    LicenseManager mgr(m_pubkeyB64, tmpDir.path(), QStringLiteral("ANY-MACHINE"));
    const QString token = readTextFile(QStringLiteral(MCU_TEST_DATA_DIR "/valid_perpetual.lic"));
    QVERIFY(!token.isEmpty());

    QVERIFY2(mgr.activate(token), qPrintable(mgr.lastError()));
    QCOMPARE(mgr.status(), QStringLiteral("active"));
    QCOMPARE(mgr.tier(), QStringLiteral("enterprise"));
    QCOMPARE(mgr.holder(), QStringLiteral("Test Perpetual User"));
    QCOMPARE(mgr.email(), QStringLiteral("perpetual@example.com"));
    QCOMPARE(mgr.seats(), 5);
    QCOMPARE(mgr.isPerpetual(), true);
    QCOMPARE(mgr.isValid(), true);
    QCOMPARE(mgr.isLicensed(), true);
    QVERIFY(mgr.hasFeature(QStringLiteral("agent")));
    QVERIFY(mgr.hasFeature(QStringLiteral("hil")));
    QVERIFY(mgr.hasFeature(QStringLiteral("updates")));
}

void TestLicense::testValidExpiring() {
    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());

    LicenseManager mgr(m_pubkeyB64, tmpDir.path(), QStringLiteral("ANY-MACHINE"));
    const QString token = readTextFile(QStringLiteral(MCU_TEST_DATA_DIR "/valid_expiring.lic"));
    QVERIFY(!token.isEmpty());

    QVERIFY2(mgr.activate(token), qPrintable(mgr.lastError()));
    QCOMPARE(mgr.status(), QStringLiteral("active"));
    QCOMPARE(mgr.tier(), QStringLiteral("pro"));
    QCOMPARE(mgr.isPerpetual(), false);
    QCOMPARE(mgr.expiresAt(), QStringLiteral("2099-01-01"));
    QVERIFY(mgr.daysRemaining() > 0);
}

void TestLicense::testExpired() {
    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());

    LicenseManager mgr(m_pubkeyB64, tmpDir.path(), QStringLiteral("ANY-MACHINE"));
    const QString token = readTextFile(QStringLiteral(MCU_TEST_DATA_DIR "/expired.lic"));
    QVERIFY(!token.isEmpty());

    QVERIFY(!mgr.activate(token));
    QVERIFY(mgr.lastError().contains(QStringLiteral("expired"), Qt::CaseInsensitive));
    QCOMPARE(mgr.isLicensed(), false);
}

void TestLicense::testMachineBound() {
    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());

    const QString token = readTextFile(QStringLiteral(MCU_TEST_DATA_DIR "/machine_bound.lic"));
    QVERIFY(!token.isEmpty());

    LicenseManager wrongMachine(m_pubkeyB64, tmpDir.path(), QStringLiteral("WRONG-MACHINE"));
    QVERIFY(!wrongMachine.activate(token));
    QVERIFY(!wrongMachine.lastError().isEmpty());

    LicenseManager rightMachine(m_pubkeyB64, tmpDir.path(), QStringLiteral("TEST-MACHINE"));
    QVERIFY2(rightMachine.activate(token), qPrintable(rightMachine.lastError()));
    QCOMPARE(rightMachine.status(), QStringLiteral("active"));
    QCOMPARE(rightMachine.tier(), QStringLiteral("team"));
    QCOMPARE(rightMachine.seats(), 3);
}

void TestLicense::testTamperedSignature() {
    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());

    LicenseManager mgr(m_pubkeyB64, tmpDir.path(), QStringLiteral("ANY-MACHINE"));
    const QString token = readTextFile(QStringLiteral(MCU_TEST_DATA_DIR "/tampered.lic"));
    QVERIFY(!token.isEmpty());

    QVERIFY(!mgr.activate(token));
    QCOMPARE(mgr.isLicensed(), false);
    QVERIFY(!mgr.lastError().isEmpty());
}

void TestLicense::testTrialLifecycleAndRollback() {
    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());

    const qint64 t0 = 1760000000;
    {
        LicenseManager mgr(m_pubkeyB64, tmpDir.path(), QStringLiteral("TRIAL-HOST"));
        mgr.setNowEpochOverride(t0);
        // Remove initial trial file created in constructor (with real time) and re-init at t0
        QFile::remove(QDir(tmpDir.path()).filePath(QStringLiteral("trial.json")));
        mgr.refresh();
        QCOMPARE(mgr.status(), QStringLiteral("trial"));
        QCOMPARE(mgr.daysRemaining(), 14);
        QCOMPARE(mgr.isValid(), true);
        QCOMPARE(mgr.isLicensed(), false);

        // +10 days
        mgr.setNowEpochOverride(t0 + 10 * 86400);
        mgr.refresh();
        QCOMPARE(mgr.status(), QStringLiteral("trial"));
        QCOMPARE(mgr.daysRemaining(), 4);
        QCOMPARE(mgr.isValid(), true);

        // Rollback clock back to t0 + 1 day -> must detect rollback (> 300s)
        mgr.setNowEpochOverride(t0 + 1 * 86400);
        mgr.refresh();
        QCOMPARE(mgr.status(), QStringLiteral("expired"));
        QCOMPARE(mgr.isValid(), false);
        QCOMPARE(mgr.daysRemaining(), 0);
        QVERIFY(mgr.lastError().contains(QStringLiteral("rollback"), Qt::CaseInsensitive));

        // Beyond 14 days
        mgr.setNowEpochOverride(t0 + 15 * 86400);
        mgr.refresh();
        QCOMPARE(mgr.status(), QStringLiteral("expired"));
        QCOMPARE(mgr.isValid(), false);
    }
}

void TestLicense::testTrialHmacTampering() {
    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());

    LicenseManager mgr(m_pubkeyB64, tmpDir.path(), QStringLiteral("TRIAL-HOST"));
    QCOMPARE(mgr.status(), QStringLiteral("trial"));

    const QString trialPath = QDir(tmpDir.path()).filePath(QStringLiteral("trial.json"));
    QFile f(trialPath);
    QVERIFY(f.open(QIODevice::ReadOnly));
    QJsonObject obj = QJsonDocument::fromJson(f.readAll()).object();
    f.close();

    // Tamper started_at without updating HMAC
    obj.insert(QStringLiteral("started_at"), obj.value(QStringLiteral("started_at")).toInteger() + 8640000);
    QVERIFY(f.open(QIODevice::WriteOnly | QIODevice::Truncate));
    f.write(QJsonDocument(obj).toJson(QJsonDocument::Compact));
    f.close();

    mgr.refresh();
    QCOMPARE(mgr.status(), QStringLiteral("invalid"));
    QCOMPARE(mgr.isValid(), false);
    QVERIFY(mgr.lastError().contains(QStringLiteral("HMAC"), Qt::CaseInsensitive));
}

QTEST_MAIN(TestLicense)
#include "tst_license.moc"
