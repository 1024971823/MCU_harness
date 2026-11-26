#include "models/ConversationModel.h"
#include "models/LogModel.h"
#include "models/WatchModel.h"

#include <QJsonObject>
#include <QTest>

class TestModels : public QObject {
    Q_OBJECT

private slots:
    void testAll27ToolTitles();
    void testConversationUpsertById();
    void testLogModelRingAndFilter();
    void testWatchModelChangeSerial();
};

void TestModels::testAll27ToolTitles() {
    const QStringList all27Tools = {
        QStringLiteral("read_file"),
        QStringLiteral("edit_file"),
        QStringLiteral("write_file"),
        QStringLiteral("list_files"),
        QStringLiteral("search_files"),
        QStringLiteral("run_shell"),
        QStringLiteral("inspect_project"),
        QStringLiteral("build_firmware"),
        QStringLiteral("generate_compile_commands"),
        QStringLiteral("analyze_footprint"),
        QStringLiteral("inspect_svd"),
        QStringLiteral("probe_control"),
        QStringLiteral("read_memory"),
        QStringLiteral("write_memory"),
        QStringLiteral("flash_firmware"),
        QStringLiteral("inspect_peripheral"),
        QStringLiteral("diagnose_peripheral"),
        QStringLiteral("inspect_board"),
        QStringLiteral("watch_variable"),
        QStringLiteral("read_debug_log"),
        QStringLiteral("diagnose_crash"),
        QStringLiteral("check_architecture"),
        QStringLiteral("scaffold_code"),
        QStringLiteral("inspect_rtos"),
        QStringLiteral("trace_target"),
        QStringLiteral("capture_logic"),
        QStringLiteral("run_hil_tests"),
    };
    QCOMPARE(all27Tools.size(), 27);
    for (const QString &t : all27Tools) {
        const QString title = ConversationModel::toolTitle(t);
        QVERIFY2(!title.isEmpty() && title != t, qPrintable(QStringLiteral("Missing toolTitle mapping for %1").arg(t)));
    }
}

void TestModels::testConversationUpsertById() {
    ConversationModel model;
    model.appendUserMessage(QStringLiteral("Check crash"));
    model.startAssistantMessage();
    model.appendAssistantDelta(QStringLiteral("Checking "));
    model.appendAssistantDelta(QStringLiteral("now..."));
    model.finishAssistantMessage();

    model.addToolCall(QStringLiteral("call-1"), QStringLiteral("diagnose_crash"), QJsonObject{{QStringLiteral("keep_halted"), false}});
    QCOMPARE(model.rowCount(), 3);
    QCOMPARE(model.data(model.index(2), ConversationModel::RoleRole).toString(), QStringLiteral("tool_call"));

    model.upsertToolResult(
        QStringLiteral("call-1"),
        QStringLiteral("diagnose_crash"),
        true,
        false,
        false,
        QJsonObject{{QStringLiteral("summary"), QStringLiteral("HardFault at main.c:42")}},
        QString());

    QCOMPARE(model.rowCount(), 3);
    QCOMPARE(model.data(model.index(2), ConversationModel::RoleRole).toString(), QStringLiteral("tool_result"));
    QCOMPARE(model.data(model.index(2), ConversationModel::OkRole).toBool(), true);
    QCOMPARE(model.data(model.index(2), ConversationModel::SummaryRole).toString(), QStringLiteral("HardFault at main.c:42"));
    QVERIFY(!model.data(model.index(2), ConversationModel::ArgsPrettyRole).toString().isEmpty());
}

void TestModels::testLogModelRingAndFilter() {
    LogModel log(100);
    QList<LogModel::Entry> batch;
    for (quint64 i = 1; i <= 150; ++i) {
        const QString txt = (i % 10 == 0)
                                ? QStringLiteral("[ERROR] HardFault line %1").arg(i)
                                : QStringLiteral("[INFO] Normal tick %1").arg(i);
        batch.append({i, 1700000000.0 + static_cast<double>(i), QString(), txt, QString()});
    }
    log.appendEntries(batch);
    QCOMPARE(log.rowCount(), 100);
    QCOMPARE(log.lastSeq(), quint64(150));
    QCOMPARE(log.data(log.index(0), LogModel::SeqRole).toULongLong(), quint64(51));

    // Duplicate seqs must be ignored
    log.appendEntry(140, 1700000140.0, QStringLiteral("duplicate"));
    QCOMPARE(log.rowCount(), 100);

    LogFilterModel filter;
    filter.setSourceModel(&log);
    QCOMPARE(filter.rowCount(), 100);

    filter.setLevelFilter(QStringLiteral("error"));
    QCOMPARE(filter.rowCount(), 10);

    filter.setLevelFilter(QStringLiteral("all"));
    filter.setRegexEnabled(true);
    filter.setFilterText(QStringLiteral("line 1[02]0$"));
    QCOMPARE(filter.rowCount(), 2);
}

void TestModels::testWatchModelChangeSerial() {
    WatchModel watch;
    QJsonObject obj;
    obj.insert(QStringLiteral("g_ticks"), QJsonObject{
        {QStringLiteral("display"), QStringLiteral("100")},
        {QStringLiteral("hex"), QStringLiteral("0x0064")},
    });
    watch.updateFromSnapshot(obj, {QStringLiteral("g_ticks")});
    QCOMPARE(watch.rowCount(), 1);
    QCOMPARE(watch.data(watch.index(0), WatchModel::ChangeSerialRole).toInt(), 0);

    // Unchanged update: changeSerial stays 0
    watch.updateFromSnapshot(obj, {QStringLiteral("g_ticks")});
    QCOMPARE(watch.data(watch.index(0), WatchModel::ChangeSerialRole).toInt(), 0);

    // Value changes: changeSerial increments to 1
    obj[QStringLiteral("g_ticks")] = QJsonObject{
        {QStringLiteral("display"), QStringLiteral("101")},
        {QStringLiteral("hex"), QStringLiteral("0x0065")},
    };
    watch.updateFromSnapshot(obj, {QStringLiteral("g_ticks")});
    QCOMPARE(watch.data(watch.index(0), WatchModel::ChangeSerialRole).toInt(), 1);
    QCOMPARE(watch.data(watch.index(0), WatchModel::ChangedRole).toBool(), true);
}

QTEST_MAIN(TestModels)
#include "tst_models.moc"
