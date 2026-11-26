#pragma once

#include <QAbstractListModel>
#include <QJsonObject>
#include <QList>
#include <QString>
#include <QStringList>

class WatchModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)

public:
    enum Roles {
        ExprRole = Qt::UserRole + 1,
        AddressHexRole,
        TypeNameRole,
        KindRole,
        DisplayRole,
        HexRole,
        ErrorRole,
        ChangedRole,
        ChangeSerialRole,
    };

    struct Entry {
        QString expr;
        QString addressHex;
        QString typeName;
        QString kind;
        QString display;
        QString hex;
        QString error;
        bool changed = false;
        int changeSerial = 0;
    };

    explicit WatchModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index, int role = Qt::DisplayRole) const override;
    QHash<int, QByteArray> roleNames() const override;

    Q_INVOKABLE void clear();
    QStringList expressions() const;
    void updateFromSnapshot(const QJsonObject &watchObj, const QStringList &exprs);

signals:
    void countChanged();

private:
    QList<Entry> m_entries;
};
