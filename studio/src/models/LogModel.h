#pragma once

#include <QAbstractListModel>
#include <QList>
#include <QSortFilterProxyModel>
#include <QString>

class LogModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)
    Q_PROPERTY(quint64 lastSeq READ lastSeq NOTIFY countChanged)

public:
    enum Roles {
        SeqRole = Qt::UserRole + 1,
        TimestampRole,
        TimeTextRole,
        TextRole,
        LevelRole,
    };

    struct Entry {
        quint64 seq = 0;
        double timestamp = 0.0;
        QString timeText;
        QString text;
        QString level; // "info" | "warn" | "error"
    };

    explicit LogModel(int maxLines = 20000, QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index, int role = Qt::DisplayRole) const override;
    QHash<int, QByteArray> roleNames() const override;

    quint64 lastSeq() const { return m_lastSeq; }

    Q_INVOKABLE void clear();
    void appendEntry(quint64 seq, double timestamp, const QString &text, const QString &level = QString());
    void appendEntries(const QList<Entry> &entries);

signals:
    void countChanged();

private:
    static QString inferLevel(const QString &text, const QString &explicitLevel);
    static QString formatTime(double timestamp);

    int m_maxLines = 20000;
    quint64 m_lastSeq = 0;
    QList<Entry> m_entries;
};

class LogFilterModel : public QSortFilterProxyModel {
    Q_OBJECT
    Q_PROPERTY(QString filterText READ filterText WRITE setFilterText NOTIFY filterChanged)
    Q_PROPERTY(bool regexEnabled READ regexEnabled WRITE setRegexEnabled NOTIFY filterChanged)
    Q_PROPERTY(QString levelFilter READ levelFilter WRITE setLevelFilter NOTIFY filterChanged)

public:
    explicit LogFilterModel(QObject *parent = nullptr);

    QString filterText() const { return m_filterText; }
    void setFilterText(const QString &text);

    bool regexEnabled() const { return m_regexEnabled; }
    void setRegexEnabled(bool enabled);

    QString levelFilter() const { return m_levelFilter; }
    void setLevelFilter(const QString &level);

signals:
    void filterChanged();

protected:
    bool filterAcceptsRow(int sourceRow, const QModelIndex &sourceParent) const override;

private:
    QString m_filterText;
    bool m_regexEnabled = false;
    QString m_levelFilter = QStringLiteral("all");
};
