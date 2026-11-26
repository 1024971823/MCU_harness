#pragma once

#include <QAbstractListModel>
#include <QJsonArray>
#include <QList>
#include <QString>

class AuditModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)

public:
    enum Roles {
        SeqRole = Qt::UserRole + 1,
        TimestampRole,
        TimeTextRole,
        OperationRole,
        TargetRole,
        LevelRole,
        AllowedRole,
        DetailRole,
    };

    struct Entry {
        quint64 seq = 0;
        double timestamp = 0.0;
        QString timeText;
        QString operation;
        QString target;
        QString level;
        bool allowed = false;
        QString detail;
    };

    explicit AuditModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index, int role = Qt::DisplayRole) const override;
    QHash<int, QByteArray> roleNames() const override;

    Q_INVOKABLE void clear();
    void updateEntries(const QJsonArray &auditArr);

signals:
    void countChanged();

private:
    QList<Entry> m_entries;
};
