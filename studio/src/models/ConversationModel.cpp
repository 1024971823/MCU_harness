#include "models/ConversationModel.h"

#include <QDateTime>
#include <QHash>
#include <QJsonDocument>
#include <QJsonValue>

namespace {

struct ToolMeta {
    const char *title;
    const char *icon;
    const char *risk;
};

const QHash<QString, ToolMeta> &toolMetaMap() {
    static const QHash<QString, ToolMeta> map = {
        {QStringLiteral("read_file"),                 {"Read File",                 "file-code-2",    "read_only"}},
        {QStringLiteral("edit_file"),                 {"Edit File",                 "file-diff",      "workspace_write"}},
        {QStringLiteral("write_file"),                {"Write File",                "file-code-2",    "workspace_write"}},
        {QStringLiteral("list_files"),                {"List Workspace Files",      "folder",         "read_only"}},
        {QStringLiteral("search_files"),              {"Search Files",              "search",         "read_only"}},
        {QStringLiteral("run_shell"),                 {"Run Shell Command",         "terminal",       "shell"}},
        {QStringLiteral("inspect_project"),           {"Inspect MCU Project",       "folder-open",    "read_only"}},
        {QStringLiteral("build_firmware"),            {"Build Firmware",            "hammer",         "workspace_write"}},
        {QStringLiteral("generate_compile_commands"), {"Generate compile_commands", "wrench",         "workspace_write"}},
        {QStringLiteral("analyze_footprint"),         {"Analyze Memory Footprint",  "hard-drive",     "read_only"}},
        {QStringLiteral("inspect_svd"),               {"Inspect SVD Register",      "binary",         "read_only"}},
        {QStringLiteral("probe_control"),             {"Probe Control",             "plug-zap",       "hardware_state"}},
        {QStringLiteral("read_memory"),               {"Read Target Memory",        "cpu",            "read_only"}},
        {QStringLiteral("write_memory"),              {"Write Target Memory",       "zap",            "hardware_danger"}},
        {QStringLiteral("flash_firmware"),            {"Flash Firmware",            "download",       "hardware_danger"}},
        {QStringLiteral("inspect_peripheral"),        {"Inspect Peripheral",        "microchip",      "read_only"}},
        {QStringLiteral("diagnose_peripheral"),       {"Diagnose Peripheral",       "waypoints",      "read_only"}},
        {QStringLiteral("inspect_board"),             {"Inspect Board Wiring",      "circuit-board",  "read_only"}},
        {QStringLiteral("watch_variable"),            {"Read Live Watch Variable",  "variable",       "read_only"}},
        {QStringLiteral("read_debug_log"),            {"Read RTT / UART Log",       "scroll-text",    "read_only"}},
        {QStringLiteral("diagnose_crash"),            {"Diagnose HardFault Crash",  "bug",            "read_only"}},
        {QStringLiteral("check_architecture"),        {"Check 4-Layer Architecture","list-checks",    "read_only"}},
        {QStringLiteral("scaffold_code"),             {"Scaffold Embedded Module",  "wand-sparkles",  "workspace_write"}},
        {QStringLiteral("inspect_rtos"),              {"Inspect RTOS Tasks",        "layers",         "read_only"}},
        {QStringLiteral("trace_target"),              {"DWT Trace & Watchpoint",    "timer",          "hardware_state"}},
        {QStringLiteral("capture_logic"),             {"Logic Analyzer & Protocol", "activity",       "read_only"}},
        {QStringLiteral("run_hil_tests"),             {"Run HIL Test Suite",        "flask-conical",  "hardware_danger"}},
    };
    return map;
}

} // namespace

ConversationModel::ConversationModel(QObject *parent)
    : QAbstractListModel(parent) {}

int ConversationModel::rowCount(const QModelIndex &parent) const {
    if (parent.isValid()) {
        return 0;
    }
    return m_entries.size();
}

QVariant ConversationModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_entries.size()) {
        return {};
    }
    const Entry &e = m_entries.at(index.row());
    switch (role) {
    case KindRole:       return e.kind;
    case RoleRole:       return e.kind;
    case TextRole:       return e.text;
    case StreamingRole:  return e.streaming;
    case ToolCallIdRole: return e.toolCallId;
    case ToolNameRole:   return e.toolName;
    case ToolTitleRole:  return e.toolTitle;
    case ToolIconRole:   return e.toolIcon;
    case RiskLevelRole:  return e.riskLevel;
    case ArgsJsonRole:   return e.argsJson;
    case ArgsPrettyRole: return e.argsJson;
    case ResultJsonRole: return e.resultJson;
    case RawJsonRole:    return e.resultJson;
    case SummaryRole:    return e.summary;
    case StatusRole:     return e.status;
    case OkRole:         return e.ok;
    case DeniedRole:     return e.denied;
    case HardDeniedRole: return e.hardDenied;
    case LevelRole:      return e.level;
    case TimestampRole:  return e.timestamp;
    case DurationMsRole: return e.durationMs;
    default:             return {};
    }
}

QHash<int, QByteArray> ConversationModel::roleNames() const {
    return {
        {KindRole,       "kind"},
        {RoleRole,       "role"},
        {TextRole,       "text"},
        {StreamingRole,  "streaming"},
        {ToolCallIdRole, "toolCallId"},
        {ToolNameRole,   "toolName"},
        {ToolTitleRole,  "toolTitle"},
        {ToolIconRole,   "toolIcon"},
        {RiskLevelRole,  "riskLevel"},
        {ArgsJsonRole,   "argsJson"},
        {ArgsPrettyRole, "argsPretty"},
        {ResultJsonRole, "resultJson"},
        {RawJsonRole,    "rawJson"},
        {SummaryRole,    "summary"},
        {StatusRole,     "status"},
        {OkRole,         "ok"},
        {DeniedRole,     "denied"},
        {HardDeniedRole, "hardDenied"},
        {LevelRole,      "level"},
        {TimestampRole,  "timestamp"},
        {DurationMsRole, "durationMs"},
    };
}

QString ConversationModel::toolTitle(const QString &toolName) {
    const auto &map = toolMetaMap();
    auto it = map.constFind(toolName);
    if (it != map.constEnd()) {
        return QString::fromUtf8(it.value().title);
    }
    return toolName;
}

QString ConversationModel::toolIcon(const QString &toolName) {
    const auto &map = toolMetaMap();
    auto it = map.constFind(toolName);
    if (it != map.constEnd()) {
        return QString::fromLatin1(it.value().icon);
    }
    return QStringLiteral("wrench");
}

QString ConversationModel::defaultRiskLevel(const QString &toolName) {
    const auto &map = toolMetaMap();
    auto it = map.constFind(toolName);
    if (it != map.constEnd()) {
        return QString::fromLatin1(it.value().risk);
    }
    return QStringLiteral("read_only");
}

QString ConversationModel::currentTimeString() {
    return QDateTime::currentDateTime().toString(QStringLiteral("HH:mm:ss"));
}

QString ConversationModel::summarizeResult(const QJsonObject &result,
                                           bool ok,
                                           bool denied,
                                           bool hardDenied,
                                           const QString &error) {
    if (denied) {
        const QString prefix = hardDenied ? QStringLiteral("HARD DENY") : QStringLiteral("DENIED");
        const QString msg = error.isEmpty() ? result.value(QStringLiteral("error")).toString(QStringLiteral("Blocked by safety interlock")) : error;
        return QStringLiteral("%1: %2").arg(prefix, msg);
    }
    if (!ok) {
        QString msg = error;
        if (msg.isEmpty()) {
            msg = result.value(QStringLiteral("error")).toString();
        }
        return msg.isEmpty() ? QStringLiteral("Tool execution failed") : msg;
    }
    if (result.contains(QStringLiteral("summary")) && result.value(QStringLiteral("summary")).isString()) {
        return result.value(QStringLiteral("summary")).toString();
    }
    if (result.contains(QStringLiteral("message")) && result.value(QStringLiteral("message")).isString()) {
        return result.value(QStringLiteral("message")).toString();
    }
    const QString compact = QString::fromUtf8(QJsonDocument(result).toJson(QJsonDocument::Compact));
    if (compact.size() <= 140) {
        return compact;
    }
    return compact.left(137) + QStringLiteral("...");
}

void ConversationModel::clear() {
    if (m_entries.isEmpty()) {
        return;
    }
    beginResetModel();
    m_entries.clear();
    endResetModel();
    emit countChanged();
}

void ConversationModel::appendUserMessage(const QString &text, const QString &timestamp) {
    Entry e;
    e.kind = QStringLiteral("user");
    e.text = text;
    e.timestamp = timestamp.isEmpty() ? currentTimeString() : timestamp;

    const int row = m_entries.size();
    beginInsertRows(QModelIndex(), row, row);
    m_entries.append(std::move(e));
    endInsertRows();
    emit countChanged();
}

void ConversationModel::startAssistantMessage(const QString &timestamp) {
    if (!m_entries.isEmpty() && m_entries.last().kind == QStringLiteral("assistant") && m_entries.last().streaming) {
        return;
    }
    Entry e;
    e.kind = QStringLiteral("assistant");
    e.streaming = true;
    e.timestamp = timestamp.isEmpty() ? currentTimeString() : timestamp;

    const int row = m_entries.size();
    beginInsertRows(QModelIndex(), row, row);
    m_entries.append(std::move(e));
    endInsertRows();
    emit countChanged();
}

void ConversationModel::appendAssistantDelta(const QString &delta) {
    if (delta.isEmpty()) {
        return;
    }
    if (m_entries.isEmpty() || m_entries.last().kind != QStringLiteral("assistant") || !m_entries.last().streaming) {
        startAssistantMessage();
    }
    const int row = m_entries.size() - 1;
    m_entries[row].text += delta;
    const QModelIndex idx = index(row);
    emit dataChanged(idx, idx, {TextRole});
}

void ConversationModel::finishAssistantMessage(const QString &finalText) {
    if (!m_entries.isEmpty() && m_entries.last().kind == QStringLiteral("assistant") && m_entries.last().streaming) {
        const int row = m_entries.size() - 1;
        m_entries[row].streaming = false;
        if (m_entries[row].text.isEmpty() && !finalText.isEmpty()) {
            m_entries[row].text = finalText;
        }
        const QModelIndex idx = index(row);
        emit dataChanged(idx, idx, {TextRole, StreamingRole});
        return;
    }
    if (!finalText.isEmpty()) {
        Entry e;
        e.kind = QStringLiteral("assistant");
        e.text = finalText;
        e.streaming = false;
        e.timestamp = currentTimeString();
        const int row = m_entries.size();
        beginInsertRows(QModelIndex(), row, row);
        m_entries.append(std::move(e));
        endInsertRows();
        emit countChanged();
    }
}

void ConversationModel::addToolCall(const QString &id,
                                    const QString &name,
                                    const QJsonObject &arguments,
                                    const QString &riskLevel) {
    // Finish any currently streaming assistant bubble before tool call card
    if (!m_entries.isEmpty() && m_entries.last().kind == QStringLiteral("assistant") && m_entries.last().streaming) {
        finishAssistantMessage();
    }

    Entry e;
    e.kind = QStringLiteral("tool_call");
    e.toolCallId = id;
    e.toolName = name;
    e.toolTitle = toolTitle(name);
    e.toolIcon = toolIcon(name);
    e.riskLevel = riskLevel.isEmpty() ? defaultRiskLevel(name) : riskLevel;
    e.argsJson = QString::fromUtf8(QJsonDocument(arguments).toJson(QJsonDocument::Indented)).trimmed();
    e.status = QStringLiteral("running");
    e.summary = tr("Running...");
    e.timestamp = currentTimeString();
    e.startedMs = QDateTime::currentMSecsSinceEpoch();

    const int row = m_entries.size();
    beginInsertRows(QModelIndex(), row, row);
    m_entries.append(std::move(e));
    endInsertRows();
    emit countChanged();
}

int ConversationModel::findToolCallRow(const QString &id) const {
    if (!id.isEmpty()) {
        for (int i = m_entries.size() - 1; i >= 0; --i) {
            if (m_entries.at(i).kind == QStringLiteral("tool_call") && m_entries.at(i).toolCallId == id) {
                return i;
            }
        }
    }
    return -1;
}

void ConversationModel::upsertToolResult(const QString &id,
                                         const QString &name,
                                         bool ok,
                                         bool denied,
                                         bool hardDenied,
                                         const QJsonObject &result,
                                         const QString &error) {
    int row = findToolCallRow(id);
    if (row < 0) {
        addToolCall(id, name, QJsonObject());
        row = m_entries.size() - 1;
    }

    Entry &e = m_entries[row];
    e.kind = QStringLiteral("tool_result");
    e.ok = ok;
    e.denied = denied;
    e.hardDenied = hardDenied;
    e.status = denied ? QStringLiteral("denied") : (ok ? QStringLiteral("ok") : QStringLiteral("error"));

    QJsonObject resToShow = result;
    if (!error.isEmpty() && !resToShow.contains(QStringLiteral("error"))) {
        resToShow.insert(QStringLiteral("error"), error);
    }
    e.resultJson = QString::fromUtf8(QJsonDocument(resToShow).toJson(QJsonDocument::Indented)).trimmed();
    e.summary = summarizeResult(result, ok, denied, hardDenied, error);
    if (e.startedMs > 0) {
        e.durationMs = qMax<qint64>(0, QDateTime::currentMSecsSinceEpoch() - e.startedMs);
    }

    const QModelIndex idx = index(row);
    emit dataChanged(idx, idx, {KindRole, RoleRole, ResultJsonRole, RawJsonRole, SummaryRole, StatusRole, OkRole, DeniedRole, HardDeniedRole, DurationMsRole});
}

void ConversationModel::addNotice(const QString &level, const QString &text) {
    Entry e;
    e.kind = QStringLiteral("notice");
    e.level = level.isEmpty() ? QStringLiteral("info") : level;
    e.text = text;
    e.timestamp = currentTimeString();

    const int row = m_entries.size();
    beginInsertRows(QModelIndex(), row, row);
    m_entries.append(std::move(e));
    endInsertRows();
    emit countChanged();
}

void ConversationModel::loadFromSessionMessages(const QJsonArray &messages) {
    beginResetModel();
    m_entries.clear();

    for (const QJsonValue &v : messages) {
        const QJsonObject msg = v.toObject();
        const QString role = msg.value(QStringLiteral("role")).toString();
        if (role == QStringLiteral("user")) {
            Entry e;
            e.kind = QStringLiteral("user");
            e.text = msg.value(QStringLiteral("content")).toString();
            m_entries.append(std::move(e));
        } else if (role == QStringLiteral("assistant")) {
            const QString content = msg.value(QStringLiteral("content")).toString();
            if (!content.isEmpty()) {
                Entry e;
                e.kind = QStringLiteral("assistant");
                e.text = content;
                e.streaming = false;
                m_entries.append(std::move(e));
            }
            const QJsonArray tcs = msg.value(QStringLiteral("tool_calls")).toArray();
            for (const QJsonValue &tcVal : tcs) {
                const QJsonObject tc = tcVal.toObject();
                const QString tcId = tc.value(QStringLiteral("id")).toString();
                const QString tcName = tc.value(QStringLiteral("name")).toString();
                const QJsonObject tcArgs = tc.value(QStringLiteral("arguments")).toObject();
                Entry te;
                te.kind = QStringLiteral("tool_call");
                te.toolCallId = tcId;
                te.toolName = tcName;
                te.toolTitle = toolTitle(tcName);
                te.toolIcon = toolIcon(tcName);
                te.riskLevel = defaultRiskLevel(tcName);
                te.argsJson = QString::fromUtf8(QJsonDocument(tcArgs).toJson(QJsonDocument::Indented)).trimmed();
                te.status = QStringLiteral("ok");
                m_entries.append(std::move(te));
            }
        } else if (role == QStringLiteral("tool")) {
            const QString tcId = msg.value(QStringLiteral("tool_call_id")).toString();
            const QString tcName = msg.value(QStringLiteral("name")).toString();
            const QString rawContent = msg.value(QStringLiteral("content")).toString();
            const QJsonDocument parsed = QJsonDocument::fromJson(rawContent.toUtf8());
            const QJsonObject resObj = parsed.isObject() ? parsed.object() : QJsonObject{{QStringLiteral("raw"), rawContent}};
            const bool denied = resObj.contains(QStringLiteral("allowed")) && !resObj.value(QStringLiteral("allowed")).toBool(true);
            const bool hardDenied = resObj.value(QStringLiteral("hard_denied")).toBool(false);
            const QString errStr = resObj.value(QStringLiteral("error")).toString();
            const bool ok = !denied && errStr.isEmpty();

            int targetRow = -1;
            for (int i = m_entries.size() - 1; i >= 0; --i) {
                if (m_entries.at(i).kind == QStringLiteral("tool_call") && m_entries.at(i).toolCallId == tcId) {
                    targetRow = i;
                    break;
                }
            }
            if (targetRow >= 0) {
                Entry &te = m_entries[targetRow];
                te.ok = ok;
                te.denied = denied;
                te.hardDenied = hardDenied;
                te.status = denied ? QStringLiteral("denied") : (ok ? QStringLiteral("ok") : QStringLiteral("error"));
                te.resultJson = QString::fromUtf8(QJsonDocument(resObj).toJson(QJsonDocument::Indented)).trimmed();
                te.summary = summarizeResult(resObj, ok, denied, hardDenied, errStr);
            } else {
                Entry te;
                te.kind = QStringLiteral("tool_call");
                te.toolCallId = tcId;
                te.toolName = tcName;
                te.toolTitle = toolTitle(tcName);
                te.toolIcon = toolIcon(tcName);
                te.riskLevel = defaultRiskLevel(tcName);
                te.ok = ok;
                te.denied = denied;
                te.hardDenied = hardDenied;
                te.status = denied ? QStringLiteral("denied") : (ok ? QStringLiteral("ok") : QStringLiteral("error"));
                te.resultJson = QString::fromUtf8(QJsonDocument(resObj).toJson(QJsonDocument::Indented)).trimmed();
                te.summary = summarizeResult(resObj, ok, denied, hardDenied, errStr);
                m_entries.append(std::move(te));
            }
        }
    }

    endResetModel();
    emit countChanged();
}
