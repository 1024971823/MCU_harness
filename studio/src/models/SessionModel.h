#pragma once

#include <QAbstractListModel>
#include <QJsonArray>
#include <QList>
#include <QString>

class SessionModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)

public:
    enum Roles {
        SessionIdRole = Qt::UserRole + 1,
        PathRole,
        MessageCountRole,
        UpdatedAtRole,
        UpdatedAtTextRole,
        RelativeTimeRole,
        PreviewRole,
        IsCurrentRole,
    };

    struct Entry {
        QString sessionId;
        QString path;
        int messageCount = 0;
        double updatedAt = 0.0;
        QString updatedAtText;
        QString relativeTime;
        QString preview;
        bool isCurrent = false;
    };

    explicit SessionModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index, int role = Qt::DisplayRole) const override;
    QHash<int, QByteArray> roleNames() const override;

    Q_INVOKABLE void clear();
    void updateSessions(const QJsonArray &sessionsArr, const QString &currentSessionId);
    void setCurrentSessionId(const QString &currentSessionId);
    void refreshLocalization();

signals:
    void countChanged();

private:
    QList<Entry> m_entries;
    QString m_currentSessionId;
};
