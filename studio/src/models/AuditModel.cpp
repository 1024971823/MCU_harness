#include "models/AuditModel.h"

#include <QDateTime>
#include <QJsonObject>

AuditModel::AuditModel(QObject *parent)
    : QAbstractListModel(parent) {}

int AuditModel::rowCount(const QModelIndex &parent) const {
    if (parent.isValid()) {
        return 0;
    }
    return m_entries.size();
}

QVariant AuditModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_entries.size()) {
        return {};
    }
    const Entry &e = m_entries.at(index.row());
    switch (role) {
    case SeqRole:       return QVariant::fromValue(e.seq);
    case TimestampRole: return e.timestamp;
    case TimeTextRole:  return e.timeText;
    case OperationRole: return e.operation;
    case TargetRole:    return e.target;
    case LevelRole:     return e.level;
    case AllowedRole:   return e.allowed;
    case DetailRole:    return e.detail;
    default:            return {};
    }
}

QHash<int, QByteArray> AuditModel::roleNames() const {
    return {
        {SeqRole,       "seq"},
        {TimestampRole, "timestamp"},
        {TimeTextRole,  "timeText"},
        {OperationRole, "operation"},
        {TargetRole,    "target"},
        {LevelRole,     "level"},
        {AllowedRole,   "allowed"},
        {DetailRole,    "detail"},
    };
}

void AuditModel::clear() {
    if (m_entries.isEmpty()) {
        return;
    }
    beginResetModel();
    m_entries.clear();
    endResetModel();
    emit countChanged();
}

void AuditModel::updateEntries(const QJsonArray &auditArr) {
    QList<Entry> next;
    next.reserve(auditArr.size());

    for (const QJsonValue &v : auditArr) {
        const QJsonObject obj = v.toObject();
        Entry e;
        e.seq = static_cast<quint64>(obj.value(QStringLiteral("seq")).toInteger(0));
        e.timestamp = obj.value(QStringLiteral("timestamp")).toDouble(0.0);
        e.timeText = e.timestamp > 0.0
                         ? QDateTime::fromMSecsSinceEpoch(static_cast<qint64>(e.timestamp * 1000.0))
                               .toLocalTime()
                               .toString(QStringLiteral("HH:mm:ss"))
                         : QStringLiteral("-");
        e.operation = obj.value(QStringLiteral("operation")).toString();
        e.target = obj.value(QStringLiteral("target")).toString();
        e.level = obj.value(QStringLiteral("level")).toString(QStringLiteral("read_only"));
        e.allowed = obj.value(QStringLiteral("allowed")).toBool(false);
        e.detail = obj.value(QStringLiteral("detail")).toString();
        next.append(std::move(e));
    }

    beginResetModel();
    m_entries = std::move(next);
    endResetModel();
    emit countChanged();
}
