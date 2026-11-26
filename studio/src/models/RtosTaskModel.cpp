#include "models/RtosTaskModel.h"

#include <QJsonObject>

RtosTaskModel::RtosTaskModel(QObject *parent)
    : QAbstractListModel(parent) {}

int RtosTaskModel::rowCount(const QModelIndex &parent) const {
    if (parent.isValid()) {
        return 0;
    }
    return m_entries.size();
}

QVariant RtosTaskModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_entries.size()) {
        return {};
    }
    const Entry &e = m_entries.at(index.row());
    switch (role) {
    case NameRole:              return e.name;
    case TcbAddressRole:        return e.tcbAddress;
    case StateRole:             return e.state;
    case PriorityRole:          return e.priority;
    case SpRole:                return e.sp;
    case StackBaseRole:         return e.stackBase;
    case StackEndRole:          return e.stackEnd;
    case StackSizeBytesRole:    return QVariant::fromValue(e.stackSizeBytes);
    case StackFreeMinBytesRole: return QVariant::fromValue(e.stackFreeMinBytes);
    case StackUsedPeakPctRole:  return e.stackUsedPeakPct;
    case StackSeverityRole:     return e.stackSeverity;
    case IsCurrentRole:         return e.isCurrent;
    default:                    return {};
    }
}

QHash<int, QByteArray> RtosTaskModel::roleNames() const {
    return {
        {NameRole,              "name"},
        {TcbAddressRole,        "tcbAddress"},
        {StateRole,             "state"},
        {PriorityRole,          "priority"},
        {SpRole,                "sp"},
        {StackBaseRole,         "stackBase"},
        {StackEndRole,          "stackEnd"},
        {StackSizeBytesRole,    "stackSizeBytes"},
        {StackFreeMinBytesRole, "stackFreeMinBytes"},
        {StackUsedPeakPctRole,  "stackUsedPeakPct"},
        {StackSeverityRole,     "stackSeverity"},
        {IsCurrentRole,         "isCurrent"},
    };
}

void RtosTaskModel::clear() {
    if (m_entries.isEmpty()) {
        return;
    }
    beginResetModel();
    m_entries.clear();
    endResetModel();
    emit countChanged();
}

void RtosTaskModel::updateTasks(const QJsonArray &tasksArr, const QString &currentTask) {
    QList<Entry> next;
    next.reserve(tasksArr.size());

    for (const QJsonValue &v : tasksArr) {
        const QJsonObject obj = v.toObject();
        Entry e;
        e.name = obj.value(QStringLiteral("name")).toString();
        e.tcbAddress = obj.value(QStringLiteral("tcb_address")).toString();
        e.state = obj.value(QStringLiteral("state")).toString(QStringLiteral("UNKNOWN"));
        e.priority = obj.value(QStringLiteral("priority")).toInt(0);
        e.sp = obj.value(QStringLiteral("sp")).toString();
        e.stackBase = obj.value(QStringLiteral("stack_base")).toString();
        e.stackEnd = obj.value(QStringLiteral("stack_end")).toString();
        e.stackSizeBytes = static_cast<quint64>(obj.value(QStringLiteral("stack_size_bytes")).toInteger(0));
        e.stackFreeMinBytes = static_cast<quint64>(obj.value(QStringLiteral("stack_free_min_bytes")).toInteger(0));
        e.stackUsedPeakPct = obj.value(QStringLiteral("stack_used_peak_pct")).toDouble(0.0);
        e.isCurrent = (!currentTask.isEmpty() && e.name == currentTask);

        if (e.stackUsedPeakPct >= 85.0) {
            e.stackSeverity = QStringLiteral("danger");
        } else if (e.stackUsedPeakPct >= 70.0) {
            e.stackSeverity = QStringLiteral("warn");
        } else {
            e.stackSeverity = QStringLiteral("ok");
        }
        next.append(std::move(e));
    }

    beginResetModel();
    m_entries = std::move(next);
    endResetModel();
    emit countChanged();
}

// ------------------------------------------------------------------------------
// RtosHazardModel
// ------------------------------------------------------------------------------

RtosHazardModel::RtosHazardModel(QObject *parent)
    : QAbstractListModel(parent) {}

int RtosHazardModel::rowCount(const QModelIndex &parent) const {
    if (parent.isValid()) {
        return 0;
    }
    return m_entries.size();
}

QVariant RtosHazardModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_entries.size()) {
        return {};
    }
    const Entry &e = m_entries.at(index.row());
    switch (role) {
    case SeverityRole: return e.severity;
    case CodeRole:     return e.code;
    case TaskNameRole: return e.taskName;
    case MessageRole:  return e.message;
    default:           return {};
    }
}

QHash<int, QByteArray> RtosHazardModel::roleNames() const {
    return {
        {SeverityRole, "severity"},
        {CodeRole,     "code"},
        {TaskNameRole, "taskName"},
        {MessageRole,  "message"},
    };
}

void RtosHazardModel::clear() {
    if (m_entries.isEmpty()) {
        return;
    }
    beginResetModel();
    m_entries.clear();
    endResetModel();
    emit countChanged();
}

void RtosHazardModel::updateHazards(const QJsonArray &hazardsArr) {
    QList<Entry> next;
    next.reserve(hazardsArr.size());

    for (const QJsonValue &v : hazardsArr) {
        const QJsonObject obj = v.toObject();
        Entry e;
        e.severity = obj.value(QStringLiteral("severity")).toString(QStringLiteral("warning"));
        e.code = obj.value(QStringLiteral("code")).toString();
        e.taskName = obj.value(QStringLiteral("task_name")).toString();
        e.message = obj.value(QStringLiteral("message")).toString();
        next.append(std::move(e));
    }

    beginResetModel();
    m_entries = std::move(next);
    endResetModel();
    emit countChanged();
}
