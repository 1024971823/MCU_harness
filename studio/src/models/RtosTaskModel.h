#pragma once

#include <QAbstractListModel>
#include <QJsonArray>
#include <QList>
#include <QString>

class RtosTaskModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)

public:
    enum Roles {
        NameRole = Qt::UserRole + 1,
        TcbAddressRole,
        StateRole,
        PriorityRole,
        SpRole,
        StackBaseRole,
        StackEndRole,
        StackSizeBytesRole,
        StackFreeMinBytesRole,
        StackUsedPeakPctRole,
        StackSeverityRole,
        IsCurrentRole,
    };

    struct Entry {
        QString name;
        QString tcbAddress;
        QString state;
        int priority = 0;
        QString sp;
        QString stackBase;
        QString stackEnd;
        quint64 stackSizeBytes = 0;
        quint64 stackFreeMinBytes = 0;
        double stackUsedPeakPct = 0.0;
        QString stackSeverity; // "ok" | "warn" | "danger"
        bool isCurrent = false;
    };

    explicit RtosTaskModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index, int role = Qt::DisplayRole) const override;
    QHash<int, QByteArray> roleNames() const override;

    Q_INVOKABLE void clear();
    void updateTasks(const QJsonArray &tasksArr, const QString &currentTask = QString());

signals:
    void countChanged();

private:
    QList<Entry> m_entries;
};

class RtosHazardModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)

public:
    enum Roles {
        SeverityRole = Qt::UserRole + 1,
        CodeRole,
        TaskNameRole,
        MessageRole,
    };

    struct Entry {
        QString severity;
        QString code;
        QString taskName;
        QString message;
    };

    explicit RtosHazardModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index, int role = Qt::DisplayRole) const override;
    QHash<int, QByteArray> roleNames() const override;

    Q_INVOKABLE void clear();
    void updateHazards(const QJsonArray &hazardsArr);

signals:
    void countChanged();

private:
    QList<Entry> m_entries;
};
