#include "models/SessionModel.h"

#include <QDateTime>
#include <QJsonObject>

SessionModel::SessionModel(QObject *parent)
    : QAbstractListModel(parent) {}

int SessionModel::rowCount(const QModelIndex &parent) const {
    if (parent.isValid()) {
        return 0;
    }
    return m_entries.size();
}

QVariant SessionModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_entries.size()) {
        return {};
    }
    const Entry &e = m_entries.at(index.row());
    switch (role) {
    case SessionIdRole:     return e.sessionId;
    case PathRole:          return e.path;
    case MessageCountRole:  return e.messageCount;
    case UpdatedAtRole:     return e.updatedAt;
    case UpdatedAtTextRole: return e.updatedAtText;
    case RelativeTimeRole: {
        if (e.updatedAt <= 0.0) {
            return QStringLiteral("-");
        }
        const qint64 delta = qMax<qint64>(0, QDateTime::currentSecsSinceEpoch() - static_cast<qint64>(e.updatedAt));
        if (delta < 60) {
            return tr("Just now");
        }
        if (delta < 3600) {
            return tr("%1m ago").arg(delta / 60);
        }
        if (delta < 86400) {
            return tr("%1h ago").arg(delta / 3600);
        }
        return tr("%1d ago").arg(delta / 86400);
    }
    case PreviewRole:       return e.preview;
    case IsCurrentRole:     return e.isCurrent;
    default:                return {};
    }
}

void SessionModel::refreshLocalization() {
    if (!m_entries.isEmpty()) {
        emit dataChanged(index(0), index(m_entries.size() - 1), {RelativeTimeRole});
    }
}

QHash<int, QByteArray> SessionModel::roleNames() const {
    return {
        {SessionIdRole,     "sessionId"},
        {PathRole,          "path"},
        {MessageCountRole,  "messageCount"},
        {UpdatedAtRole,     "updatedAt"},
        {UpdatedAtTextRole, "updatedAtText"},
        {RelativeTimeRole,  "relativeTime"},
        {PreviewRole,       "preview"},
        {IsCurrentRole,     "isCurrent"},
    };
}

void SessionModel::clear() {
    if (m_entries.isEmpty()) {
        return;
    }
    beginResetModel();
    m_entries.clear();
    endResetModel();
    emit countChanged();
}

void SessionModel::updateSessions(const QJsonArray &sessionsArr, const QString &currentSessionId) {
    m_currentSessionId = currentSessionId;
    QList<Entry> next;
    next.reserve(sessionsArr.size());

    for (const QJsonValue &v : sessionsArr) {
        const QJsonObject obj = v.toObject();
        Entry e;
        e.sessionId = obj.value(QStringLiteral("session_id")).toString();
        e.path = obj.value(QStringLiteral("path")).toString();
        e.messageCount = obj.value(QStringLiteral("message_count")).toInt(0);
        e.updatedAt = obj.value(QStringLiteral("updated_at")).toDouble(0.0);
        e.updatedAtText = e.updatedAt > 0.0
                              ? QDateTime::fromMSecsSinceEpoch(static_cast<qint64>(e.updatedAt * 1000.0))
                                    .toLocalTime()
                                    .toString(QStringLiteral("yyyy-MM-dd HH:mm"))
                              : QStringLiteral("-");
        if (e.updatedAt > 0.0) {
            const qint64 delta = qMax<qint64>(0, QDateTime::currentSecsSinceEpoch() - static_cast<qint64>(e.updatedAt));
            if (delta < 60) {
                e.relativeTime = tr("Just now");
            } else if (delta < 3600) {
                e.relativeTime = tr("%1m ago").arg(delta / 60);
            } else if (delta < 86400) {
                e.relativeTime = tr("%1h ago").arg(delta / 3600);
            } else {
                e.relativeTime = tr("%1d ago").arg(delta / 86400);
            }
        } else {
            e.relativeTime = QStringLiteral("-");
        }
        e.preview = obj.value(QStringLiteral("preview")).toString();
        e.isCurrent = (!m_currentSessionId.isEmpty() && e.sessionId == m_currentSessionId);
        next.append(std::move(e));
    }

    beginResetModel();
    m_entries = std::move(next);
    endResetModel();
    emit countChanged();
}

void SessionModel::setCurrentSessionId(const QString &currentSessionId) {
    m_currentSessionId = currentSessionId;
    for (int i = 0; i < m_entries.size(); ++i) {
        const bool cur = (!m_currentSessionId.isEmpty() && m_entries.at(i).sessionId == m_currentSessionId);
        if (m_entries.at(i).isCurrent != cur) {
            m_entries[i].isCurrent = cur;
            const QModelIndex idx = index(i);
            emit dataChanged(idx, idx, {IsCurrentRole});
        }
    }
}
