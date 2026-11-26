#include "models/PlaybookModel.h"

#include <QJsonObject>

PlaybookModel::PlaybookModel(QObject *parent)
    : QAbstractListModel(parent) {
    loadDefaults();
}

int PlaybookModel::rowCount(const QModelIndex &parent) const {
    if (parent.isValid()) {
        return 0;
    }
    return m_entries.size();
}

QVariant PlaybookModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_entries.size()) {
        return {};
    }
    const Entry &e = m_entries.at(index.row());
    auto localizedTitle = [&]() -> QString {
        if (e.name == QLatin1String("crash"))    return tr("HardFault & Crash Root-Cause SOP");
        if (e.name == QLatin1String("doctor"))   return tr("4-Step Peripheral Doctor");
        if (e.name == QLatin1String("bringup"))  return tr("New Board Bring-Up Checkup");
        if (e.name == QLatin1String("refactor")) return tr("Legacy Firmware Architecture Audit & Refactoring");
        if (e.name == QLatin1String("hil"))      return tr("Build-Flash-Test HIL Regression Loop");
        return e.title;
    };
    auto localizedDesc = [&]() -> QString {
        if (e.name == QLatin1String("crash"))    return tr("Automated Cortex-M fault decoding, RTOS task inspection, RTT/UART log correlation, and C line attribution.");
        if (e.name == QLatin1String("doctor"))   return tr("Four-step peripheral diagnostic linking board wiring, RCC clock, NVIC, GPIO AF, and live SVD diff.");
        if (e.name == QLatin1String("bringup"))  return tr("End-to-end initial board checkup: project profile, board wiring, probe attachment, clock frequency, and boot logs.");
        if (e.name == QLatin1String("refactor")) return tr("Analyze compile database, 4-layer architecture & ISR rules, memory footprint, and scaffold clean layers.");
        if (e.name == QLatin1String("hil"))      return tr("Closed-loop compile, HITL-approved firmware flash, and declarative Hardware-in-the-Loop test suite execution.");
        return e.description;
    };
    switch (role) {
    case NameRole:             return e.name;
    case CommandRole:          return e.command;
    case TitleRole:            return localizedTitle();
    case DescriptionRole:      return localizedDesc();
    case UsageRole:            return e.usage;
    case RecommendedToolsRole: return e.recommendedTools;
    default:                   return {};
    }
}

void PlaybookModel::refreshLocalization() {
    if (!m_entries.isEmpty()) {
        emit dataChanged(index(0), index(m_entries.size() - 1), {TitleRole, DescriptionRole});
    }
}

QHash<int, QByteArray> PlaybookModel::roleNames() const {
    return {
        {NameRole,             "name"},
        {CommandRole,          "command"},
        {TitleRole,            "title"},
        {DescriptionRole,      "description"},
        {UsageRole,            "usage"},
        {RecommendedToolsRole, "recommendedTools"},
    };
}

void PlaybookModel::loadDefaults() {
    beginResetModel();
    m_entries = {
        {
            QStringLiteral("crash"),
            QStringLiteral("/crash"),
            tr("HardFault & Crash Root-Cause SOP"),
            tr("Automated Cortex-M fault decoding, RTOS task inspection, RTT/UART log correlation, and C line attribution."),
            QStringLiteral("/crash [optional context]"),
            {QStringLiteral("diagnose_crash"), QStringLiteral("inspect_rtos"), QStringLiteral("read_debug_log"), QStringLiteral("read_memory")},
        },
        {
            QStringLiteral("doctor"),
            QStringLiteral("/doctor"),
            tr("4-Step Peripheral Doctor"),
            tr("Four-step peripheral diagnostic linking board wiring, RCC clock, NVIC, GPIO AF, and live SVD diff."),
            QStringLiteral("/doctor [PERIPH]"),
            {QStringLiteral("inspect_board"), QStringLiteral("diagnose_peripheral"), QStringLiteral("inspect_peripheral"), QStringLiteral("read_debug_log")},
        },
        {
            QStringLiteral("bringup"),
            QStringLiteral("/bringup"),
            tr("New Board Bring-Up Checkup"),
            tr("End-to-end initial board checkup: project profile, board wiring, probe attachment, clock frequency, and boot logs."),
            QStringLiteral("/bringup [optional focus]"),
            {QStringLiteral("inspect_project"), QStringLiteral("inspect_board"), QStringLiteral("probe_control"), QStringLiteral("trace_target"), QStringLiteral("read_debug_log")},
        },
        {
            QStringLiteral("refactor"),
            QStringLiteral("/refactor"),
            tr("Legacy Firmware Architecture Audit & Refactoring"),
            tr("Analyze compile database, 4-layer architecture & ISR rules, memory footprint, and scaffold clean layers."),
            QStringLiteral("/refactor [optional scope]"),
            {QStringLiteral("generate_compile_commands"), QStringLiteral("check_architecture"), QStringLiteral("analyze_footprint"), QStringLiteral("scaffold_code")},
        },
        {
            QStringLiteral("hil"),
            QStringLiteral("/hil"),
            tr("Build-Flash-Test HIL Regression Loop"),
            tr("Closed-loop compile, HITL-approved firmware flash, and declarative Hardware-in-the-Loop test suite execution."),
            QStringLiteral("/hil <spec>"),
            {QStringLiteral("build_firmware"), QStringLiteral("flash_firmware"), QStringLiteral("run_hil_tests"), QStringLiteral("read_debug_log")},
        },
    };
    endResetModel();
    emit countChanged();
}

void PlaybookModel::updatePlaybooks(const QJsonArray &playbooksArr) {
    if (playbooksArr.isEmpty()) {
        return;
    }
    QList<Entry> next;
    next.reserve(playbooksArr.size());

    for (const QJsonValue &v : playbooksArr) {
        const QJsonObject obj = v.toObject();
        Entry e;
        e.name = obj.value(QStringLiteral("name")).toString();
        e.command = obj.value(QStringLiteral("command")).toString();
        e.title = obj.value(QStringLiteral("title")).toString();
        e.description = obj.value(QStringLiteral("description")).toString();
        e.usage = obj.value(QStringLiteral("usage")).toString();
        const QJsonArray tools = obj.value(QStringLiteral("recommended_tools")).toArray();
        for (const QJsonValue &tv : tools) {
            e.recommendedTools.append(tv.toString());
        }
        next.append(std::move(e));
    }

    beginResetModel();
    m_entries = std::move(next);
    endResetModel();
    emit countChanged();
}
