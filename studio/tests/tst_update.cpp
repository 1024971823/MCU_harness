#include "core/UpdateManager.h"

#include <QDir>
#include <QFile>
#include <QProcess>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTest>
#include <QUrl>

namespace {

QByteArray readAllBytes(const QString &path) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) {
        return {};
    }
    return f.readAll();
}

} // namespace

class TestUpdate : public QObject {
    Q_OBJECT

private slots:
    void initTestCase();
    void testSemverCompare_data();
    void testSemverCompare();
    void testManifestSignatureVerify();
    void testCheckManifestOkAndBadsig();
    void testDownloadAndSha256FileUrl();

private:
    QString m_pubkeyB64;
};

void TestUpdate::initTestCase() {
    m_pubkeyB64 = QString::fromUtf8(readAllBytes(QStringLiteral(MCU_TEST_DATA_DIR "/test_pubkey.b64"))).trimmed();
    QVERIFY2(!m_pubkeyB64.isEmpty(), "Missing test_pubkey.b64");
}

void TestUpdate::testSemverCompare_data() {
    QTest::addColumn<QString>("a");
    QTest::addColumn<QString>("b");
    QTest::addColumn<int>("expected");

    QTest::newRow("equal") << "1.0.0" << "1.0.0" << 0;
    QTest::newRow("patch_greater") << "1.0.2" << "1.0.1" << 1;
    QTest::newRow("minor_double_digit") << "1.10.0" << "1.2.0" << 1;
    QTest::newRow("major_less") << "1.9.9" << "2.0.0" << -1;
    QTest::newRow("v_prefix") << "v1.2.3" << "1.2.3" << 0;
    QTest::newRow("prerelease_less") << "1.2.0-rc.1" << "1.2.0" << -1;
}

void TestUpdate::testSemverCompare() {
    QFETCH(QString, a);
    QFETCH(QString, b);
    QFETCH(int, expected);
    QCOMPARE(UpdateManager::compareSemver(a, b), expected);
}

void TestUpdate::testManifestSignatureVerify() {
    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());
    UpdateManager mgr(m_pubkeyB64, QStringLiteral("1.0.0"), tmpDir.path());

    const QByteArray okBytes = readAllBytes(QStringLiteral(MCU_TEST_DATA_DIR "/manifest_ok.json"));
    const QByteArray okSig = readAllBytes(QStringLiteral(MCU_TEST_DATA_DIR "/manifest_ok.json.sig"));
    QVERIFY(!okBytes.isEmpty());
    QVERIFY(mgr.verifyManifestSignature(okBytes, okSig));

    const QByteArray badBytes = readAllBytes(QStringLiteral(MCU_TEST_DATA_DIR "/manifest_badsig.json"));
    const QByteArray badSig = readAllBytes(QStringLiteral(MCU_TEST_DATA_DIR "/manifest_badsig.json.sig"));
    QVERIFY(!mgr.verifyManifestSignature(badBytes, badSig));

    // Also verify SHA256 helper against fake_asset.bin
    const QString fakeBin = QStringLiteral(MCU_TEST_DATA_DIR "/fake_asset.bin");
    QVERIFY(UpdateManager::verifyFileSha256AndSize(
        fakeBin,
        QStringLiteral("684a34e3c2ab022a18207035da97abef5565dbdf41a7b9a65b30cb11cfd77e7e"),
        36));
    QVERIFY(!UpdateManager::verifyFileSha256AndSize(
        fakeBin,
        QStringLiteral("0000000000000000000000000000000000000000000000000000000000000000"),
        36));
    QVERIFY(!UpdateManager::verifyFileSha256AndSize(
        fakeBin,
        QStringLiteral("684a34e3c2ab022a18207035da97abef5565dbdf41a7b9a65b30cb11cfd77e7e"),
        999));
}

void TestUpdate::testCheckManifestOkAndBadsig() {
    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());

    {
        UpdateManager mgr(m_pubkeyB64, QStringLiteral("1.0.0"), tmpDir.path());
        QSignalSpy spy(&mgr, &UpdateManager::updateCheckFinished);
        const QUrl okUrl = QUrl::fromLocalFile(QStringLiteral(MCU_TEST_DATA_DIR "/manifest_ok.json"));
        mgr.checkForUpdates(okUrl.toString());
        QVERIFY(spy.wait(5000));
        QCOMPARE(mgr.updateAvailable(), true);
        QCOMPARE(mgr.latestVersion(), QStringLiteral("1.2.3"));
        QVERIFY(mgr.errorMessage().isEmpty());
        QVERIFY(mgr.releaseNotes().contains(QStringLiteral("Test release notes EN")));
    }

    {
        UpdateManager mgr(m_pubkeyB64, QStringLiteral("1.0.0"), tmpDir.path());
        QSignalSpy spy(&mgr, &UpdateManager::updateCheckFinished);
        const QUrl badUrl = QUrl::fromLocalFile(QStringLiteral(MCU_TEST_DATA_DIR "/manifest_badsig.json"));
        mgr.checkForUpdates(badUrl.toString());
        QVERIFY(spy.wait(5000));
        QCOMPARE(mgr.updateAvailable(), false);
        QVERIFY(!mgr.errorMessage().isEmpty());
    }
}

void TestUpdate::testDownloadAndSha256FileUrl() {
    const QString makerScript = QStringLiteral(MCU_REPO_ROOT "/tools/release/make_update_manifest.py");
    if (!QFile::exists(makerScript)) {
        QSKIP("make_update_manifest.py not present");
    }

    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());

    const QString notesEn = tmpDir.filePath(QStringLiteral("en.md"));
    const QString notesZh = tmpDir.filePath(QStringLiteral("zh.md"));
    {
        QFile f1(notesEn);
        QVERIFY(f1.open(QIODevice::WriteOnly));
        f1.write("EN notes\n");
        QFile f2(notesZh);
        QVERIFY(f2.open(QIODevice::WriteOnly));
        f2.write("ZH notes\n");
    }

    const QString assetPath = QStringLiteral(MCU_TEST_DATA_DIR "/fake_asset.bin");
    const QString assetUrl = QUrl::fromLocalFile(assetPath).toString();
    const QString manifestOut = tmpDir.filePath(QStringLiteral("local_manifest.json"));

    QProcess gen;
    gen.start(QStringLiteral("python3"), {
        makerScript,
        QStringLiteral("--key"), QStringLiteral(MCU_TEST_DATA_DIR "/test_ed25519_private.pem"),
        QStringLiteral("--version"), QStringLiteral("1.2.3"),
        QStringLiteral("--notes-en"), notesEn,
        QStringLiteral("--notes-zh"), notesZh,
        QStringLiteral("--asset"), QStringLiteral("linux-x64=%1=%2").arg(assetPath, assetUrl),
        QStringLiteral("--out"), manifestOut,
    });
    if (!gen.waitForFinished(5000) || gen.exitCode() != 0) {
        QSKIP("cryptography Python package not available to sign local manifest");
    }

    UpdateManager mgr(m_pubkeyB64, QStringLiteral("1.0.0"), tmpDir.filePath(QStringLiteral("dl")));
    QSignalSpy checkSpy(&mgr, &UpdateManager::updateCheckFinished);
    mgr.checkForUpdates(QUrl::fromLocalFile(manifestOut).toString());
    QVERIFY(checkSpy.wait(5000));
    QCOMPARE(mgr.updateAvailable(), true);

    QSignalSpy dlSpy(&mgr, &UpdateManager::downloadFinished);
    mgr.downloadUpdate();
    QVERIFY(dlSpy.wait(5000));
    QCOMPARE(dlSpy.at(0).at(0).toBool(), true);
    const QString downloaded = mgr.downloadedFilePath();
    QVERIFY(QFile::exists(downloaded));
    QCOMPARE(readAllBytes(downloaded), readAllBytes(assetPath));
}

QTEST_MAIN(TestUpdate)
#include "tst_update.moc"
