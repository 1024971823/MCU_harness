#include "models/LogModel.h"

#include <QDateTime>
#include <QRegularExpression>

LogModel::LogModel(int maxLines, QObject *parent)
    : QAbstractListModel(parent)
    , m_maxLines(qMax(100, maxLines)) {}

int LogModel::rowCount(const QModelIndex &parent) const {
    if (parent.isValid()) {
        return 0;
    }
    return m_entries.size();
}

QVariant LogModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_entries.size()) {
        return {};
    }
    const Entry &e = m_entries.at(index.row());
    switch (role) {
    case SeqRole:       return QVariant::fromValue(e.seq);
    case TimestampRole: return e.timestamp;
    case TimeTextRole:  return e.timeText;
    case TextRole:      return e.text;
    case LevelRole:     return e.level;
    default:            return {};
    }
}

QHash<int, QByteArray> LogModel::roleNames() const {
    return {
        {SeqRole,       "seq"},
        {TimestampRole, "timestamp"},
        {TimeTextRole,  "timeText"},
        {TextRole,      "text"},
        {LevelRole,     "level"},
    };
}

QString LogModel::inferLevel(const QString &text, const QString &explicitLevel) {
    if (!explicitLevel.isEmpty()) {
        return explicitLevel;
    }
    const QString lower = text.toLower();
    if (lower.contains(QStringLiteral("error")) || lower.contains(QStringLiteral("fault")) ||
        lower.contains(QStringLiteral("assert")) || lower.contains(QStringLiteral("panic"))) {
        return QStringLiteral("error");
    }
    if (lower.contains(QStringLiteral("warn"))) {
        return QStringLiteral("warn");
    }
    return QStringLiteral("info");
}

QString LogModel::formatTime(double timestamp) {
    if (timestamp <= 0.0) {
        return QDateTime::currentDateTime().toString(QStringLiteral("HH:mm:ss.zzz"));
    }
    return QDateTime::fromMSecsSinceEpoch(static_cast<qint64>(timestamp * 1000.0))
        .toLocalTime()
        .toString(QStringLiteral("HH:mm:ss.zzz"));
}

void LogModel::clear() {
    if (m_entries.isEmpty() && m_lastSeq == 0) {
        return;
    }
    beginResetModel();
    m_entries.clear();
    m_lastSeq = 0;
    endResetModel();
    emit countChanged();
}

void LogModel::appendEntry(quint64 seq, double timestamp, const QString &text, const QString &level) {
    appendEntries({Entry{seq, timestamp, formatTime(timestamp), text, inferLevel(text, level)}});
}

void LogModel::appendEntries(const QList<Entry> &entries) {
    if (entries.isEmpty()) {
        return;
    }

    QList<Entry> normalized;
    normalized.reserve(entries.size());
    for (Entry e : entries) {
        if (e.seq > 0 && e.seq <= m_lastSeq) {
            continue;
        }
        if (e.timeText.isEmpty()) {
            e.timeText = formatTime(e.timestamp);
        }
        e.level = inferLevel(e.text, e.level);
        if (e.seq > m_lastSeq) {
            m_lastSeq = e.seq;
        }
        normalized.append(std::move(e));
    }

    if (normalized.isEmpty()) {
        return;
    }

    if (normalized.size() >= m_maxLines) {
        beginResetModel();
        m_entries = normalized.mid(normalized.size() - m_maxLines);
        endResetModel();
        emit countChanged();
        return;
    }

    const int overflow = (m_entries.size() + normalized.size()) - m_maxLines;
    if (overflow > 0) {
        beginRemoveRows(QModelIndex(), 0, overflow - 1);
        m_entries.remove(0, overflow);
        endRemoveRows();
    }

    const int first = m_entries.size();
    const int last = first + normalized.size() - 1;
    beginInsertRows(QModelIndex(), first, last);
    m_entries.append(normalized);
    endInsertRows();
    emit countChanged();
}

// ------------------------------------------------------------------------------
// LogFilterModel
// ------------------------------------------------------------------------------

LogFilterModel::LogFilterModel(QObject *parent)
    : QSortFilterProxyModel(parent) {}

void LogFilterModel::setFilterText(const QString &text) {
    if (m_filterText != text) {
#if QT_VERSION >= QT_VERSION_CHECK(6, 10, 0)
        beginFilterChange();
        m_filterText = text;
        endFilterChange(QSortFilterProxyModel::Direction::Rows);
#else
        m_filterText = text;
        invalidateFilter();
#endif
        emit filterChanged();
    }
}

void LogFilterModel::setRegexEnabled(bool enabled) {
    if (m_regexEnabled != enabled) {
#if QT_VERSION >= QT_VERSION_CHECK(6, 10, 0)
        beginFilterChange();
        m_regexEnabled = enabled;
        endFilterChange(QSortFilterProxyModel::Direction::Rows);
#else
        m_regexEnabled = enabled;
        invalidateFilter();
#endif
        emit filterChanged();
    }
}

void LogFilterModel::setLevelFilter(const QString &level) {
    const QString norm = level.trimmed().isEmpty() ? QStringLiteral("all") : level.trimmed().toLower();
    if (m_levelFilter != norm) {
#if QT_VERSION >= QT_VERSION_CHECK(6, 10, 0)
        beginFilterChange();
        m_levelFilter = norm;
        endFilterChange(QSortFilterProxyModel::Direction::Rows);
#else
        m_levelFilter = norm;
        invalidateFilter();
#endif
        emit filterChanged();
    }
}

bool LogFilterModel::filterAcceptsRow(int sourceRow, const QModelIndex &sourceParent) const {
    const QModelIndex idx = sourceModel()->index(sourceRow, 0, sourceParent);
    if (!idx.isValid()) {
        return false;
    }

    if (m_levelFilter != QStringLiteral("all")) {
        const QString rowLevel = sourceModel()->data(idx, LogModel::LevelRole).toString();
        if (m_levelFilter == QStringLiteral("error") && rowLevel != QStringLiteral("error")) {
            return false;
        }
        if (m_levelFilter == QStringLiteral("warn") &&
            rowLevel != QStringLiteral("warn") && rowLevel != QStringLiteral("error")) {
            return false;
        }
    }

    const QString q = m_filterText.trimmed();
    if (q.isEmpty()) {
        return true;
    }

    const QString text = sourceModel()->data(idx, LogModel::TextRole).toString();
    if (m_regexEnabled) {
        const QRegularExpression re(q, QRegularExpression::CaseInsensitiveOption);
        if (re.isValid()) {
            return re.match(text).hasMatch();
        }
    }
    return text.contains(q, Qt::CaseInsensitive);
}
