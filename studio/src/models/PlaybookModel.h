#pragma once

#include <QAbstractListModel>
#include <QJsonArray>
#include <QList>
#include <QString>
#include <QStringList>

class PlaybookModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)

public:
    enum Roles {
        NameRole = Qt::UserRole + 1,
        CommandRole,
        TitleRole,
        DescriptionRole,
        UsageRole,
        RecommendedToolsRole,
    };

    struct Entry {
        QString name;
        QString command;
        QString title;
        QString description;
        QString usage;
        QStringList recommendedTools;
    };

    explicit PlaybookModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index, int role = Qt::DisplayRole) const override;
    QHash<int, QByteArray> roleNames() const override;

    void loadDefaults();
    void updatePlaybooks(const QJsonArray &playbooksArr);
    void refreshLocalization();

signals:
    void countChanged();

private:
    QList<Entry> m_entries;
};
