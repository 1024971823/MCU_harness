#pragma once

#include <QAbstractListModel>
#include <QJsonArray>
#include <QJsonObject>
#include <QList>
#include <QString>

class ConversationModel : public QAbstractListModel {
    Q_OBJECT
    Q_PROPERTY(int count READ rowCount NOTIFY countChanged)

public:
    enum Roles {
        KindRole = Qt::UserRole + 1,
        RoleRole,
        TextRole,
        StreamingRole,
        ToolCallIdRole,
        ToolNameRole,
        ToolTitleRole,
        ToolIconRole,
        RiskLevelRole,
        ArgsJsonRole,
        ArgsPrettyRole,
        ResultJsonRole,
        RawJsonRole,
        SummaryRole,
        StatusRole,
        OkRole,
        DeniedRole,
        HardDeniedRole,
        LevelRole,
        TimestampRole,
        DurationMsRole,
    };

    struct Entry {
        QString kind; // "user" | "assistant" | "tool_call" | "notice"
        QString text;
        bool streaming = false;
        QString toolCallId;
        QString toolName;
        QString toolTitle;
        QString toolIcon;
        QString riskLevel;
        QString argsJson;
        QString resultJson;
        QString summary;
        QString status; // "running" | "ok" | "error" | "denied"
        bool ok = false;
        bool denied = false;
        bool hardDenied = false;
        QString level; // "info" | "warning" | "error"
        QString timestamp;
        qint64 durationMs = 0;
        qint64 startedMs = 0;
    };

    explicit ConversationModel(QObject *parent = nullptr);

    int rowCount(const QModelIndex &parent = QModelIndex()) const override;
    QVariant data(const QModelIndex &index, int role = Qt::DisplayRole) const override;
    QHash<int, QByteArray> roleNames() const override;

    Q_INVOKABLE void clear();
    void appendUserMessage(const QString &text, const QString &timestamp = QString());
    void startAssistantMessage(const QString &timestamp = QString());
    void appendAssistantDelta(const QString &delta);
    void finishAssistantMessage(const QString &finalText = QString());
    void addToolCall(const QString &id,
                     const QString &name,
                     const QJsonObject &arguments,
                     const QString &riskLevel = QString());
    void upsertToolResult(const QString &id,
                          const QString &name,
                          bool ok,
                          bool denied,
                          bool hardDenied,
                          const QJsonObject &result,
                          const QString &error);
    void addNotice(const QString &level, const QString &text);
    void loadFromSessionMessages(const QJsonArray &messages);

    static QString toolTitle(const QString &toolName);
    static QString toolIcon(const QString &toolName);
    static QString defaultRiskLevel(const QString &toolName);

signals:
    void countChanged();

private:
    int findToolCallRow(const QString &id) const;
    static QString currentTimeString();
    static QString summarizeResult(const QJsonObject &result, bool ok, bool denied, bool hardDenied, const QString &error);

    QList<Entry> m_entries;
};
