#include "backend/EngineClient.h"

#include <QJsonObject>
#include <QSignalSpy>
#include <QTemporaryDir>
#include <QTest>

class TestEngineClient : public QObject {
    Q_OBJECT

private slots:
    void parseNdjson_data();
    void parseNdjson();
    void discoverRepoAndPython();
    void realSubprocessMockLlm();
};

void TestEngineClient::parseNdjson_data() {
    QTest::addColumn<QByteArray>("line");
    QTest::addColumn<bool>("expectValid");
    QTest::addColumn<int>("expectType");
    QTest::addColumn<qint64>("expectId");
    QTest::addColumn<bool>("expectOk");
    QTest::addColumn<QString>("expectEvent");

    QTest::newRow("response_ok")
        << QByteArray(R"({"id": 42, "ok": true, "result": {"pong": true}})")
        << true << static_cast<int>(EngineClient::FrameType::Response) << qint64(42) << true << QString();

    QTest::newRow("response_err")
        << QByteArray(R"({"id": 7, "ok": false, "error": "boom"})")
        << true << static_cast<int>(EngineClient::FrameType::Response) << qint64(7) << false << QString();

    QTest::newRow("event_standard")
        << QByteArray(R"({"event": "text_delta", "seq": 5, "timestamp": 1.5, "data": {"delta": "hi"}})")
        << true << static_cast<int>(EngineClient::FrameType::Event) << qint64(-1) << false << QStringLiteral("text_delta");

    QTest::newRow("event_kind_alias")
        << QByteArray(R"({"kind": "turn_completed", "seq": 9, "data": {"text": "done"}})")
        << true << static_cast<int>(EngineClient::FrameType::Event) << qint64(-1) << false << QStringLiteral("turn_completed");

    QTest::newRow("empty_line")
        << QByteArray("   \n")
        << false << static_cast<int>(EngineClient::FrameType::Invalid) << qint64(-1) << false << QString();

    QTest::newRow("invalid_json")
        << QByteArray("{not-json")
        << false << static_cast<int>(EngineClient::FrameType::Invalid) << qint64(-1) << false << QString();

    QTest::newRow("missing_fields")
        << QByteArray(R"({"foo": "bar"})")
        << false << static_cast<int>(EngineClient::FrameType::Invalid) << qint64(-1) << false << QString();
}

void TestEngineClient::parseNdjson() {
    QFETCH(QByteArray, line);
    QFETCH(bool, expectValid);
    QFETCH(int, expectType);
    QFETCH(qint64, expectId);
    QFETCH(bool, expectOk);
    QFETCH(QString, expectEvent);

    EngineClient::ParsedFrame frame;
    QString err;
    const bool valid = EngineClient::parseNdjsonLine(line, &frame, &err);
    QCOMPARE(valid, expectValid);
    if (expectValid) {
        QCOMPARE(static_cast<int>(frame.type), expectType);
        if (frame.type == EngineClient::FrameType::Response) {
            QCOMPARE(frame.id, expectId);
            QCOMPARE(frame.ok, expectOk);
        } else {
            QCOMPARE(frame.eventKind, expectEvent);
        }
    }
}

void TestEngineClient::discoverRepoAndPython() {
    const QString root = EngineClient::discoverRepoRoot(QStringLiteral(MCU_REPO_ROOT));
    QVERIFY2(!root.isEmpty(), "Expected discoverRepoRoot to locate src/mcu_harness");
    const QString py = EngineClient::discoverPythonBinary(root);
    QVERIFY2(!py.isEmpty(), "Expected non-empty Python executable");
}

void TestEngineClient::realSubprocessMockLlm() {
    QTemporaryDir tmpDir;
    QVERIFY(tmpDir.isValid());

    EngineClient client;
    client.setAutoRestart(false);

    QSignalSpy readySpy(&client, &EngineClient::readyReceived);
    QSignalSpy eventSpy(&client, &EngineClient::eventReceived);

    EngineClient::LaunchConfig cfg;
    cfg.repoRoot = QStringLiteral(MCU_REPO_ROOT);
    cfg.projectDir = tmpDir.path();
    cfg.mode = QStringLiteral("interactive");
    cfg.mockLlm = true;

    client.start(cfg);
    QVERIFY2(readySpy.wait(10000), "Engine did not emit 'ready' within 10s");
    QVERIFY(client.isRunning());

    // 1. Ping
    bool pingDone = false;
    bool pingOk = false;
    QJsonObject pingRes;
    client.sendRequest(QStringLiteral("ping"), QJsonObject(), [&](bool ok, const QJsonObject &res, const QString &) {
        pingDone = true;
        pingOk = ok;
        pingRes = res;
    });
    QTRY_VERIFY_WITH_TIMEOUT(pingDone, 5000);
    QVERIFY(pingOk);
    QCOMPARE(pingRes.value(QStringLiteral("pong")).toBool(), true);

    // 2. Chat turn with mock-llm
    bool chatAccepted = false;
    bool turnCompleted = false;
    QObject::connect(&client, &EngineClient::eventReceived, &client,
                     [&](const QString &kind, quint64, double, const QJsonObject &) {
                         if (kind == QStringLiteral("turn_completed")) {
                             turnCompleted = true;
                         }
                     });

    client.sendRequest(
        QStringLiteral("chat"),
        QJsonObject{{QStringLiteral("message"), QStringLiteral("Inspect this MCU project")}},
        [&](bool ok, const QJsonObject &res, const QString &) {
            chatAccepted = ok && res.value(QStringLiteral("accepted")).toBool();
        });
    QTRY_VERIFY_WITH_TIMEOUT(chatAccepted, 5000);
    QTRY_VERIFY_WITH_TIMEOUT(turnCompleted, 10000);

    // 3. Clean shutdown with zero orphan process
    client.shutdown();
    QVERIFY(!client.isRunning());
}

QTEST_MAIN(TestEngineClient)
#include "tst_engineclient.moc"
