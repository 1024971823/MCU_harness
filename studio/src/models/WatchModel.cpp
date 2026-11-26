#include "models/WatchModel.h"

#include <QHash>
#include <QJsonDocument>
#include <QJsonValue>

namespace {

QString compactScalar(const QJsonValue &v) {
    if (v.isNull() || v.isUndefined()) {
        return QStringLiteral("-");
    }
    if (v.isBool()) {
        return v.toBool() ? QStringLiteral("true") : QStringLiteral("false");
    }
    if (v.isDouble()) {
        const double d = v.toDouble();
        if (d == static_cast<double>(static_cast<qint64>(d))) {
            return QString::number(static_cast<qint64>(d));
        }
        return QString::number(d);
    }
    if (v.isString()) {
        return v.toString();
    }
    return QString::fromUtf8(QJsonDocument(v.toObject()).toJson(QJsonDocument::Compact));
}

} // namespace

WatchModel::WatchModel(QObject *parent)
    : QAbstractListModel(parent) {}

int WatchModel::rowCount(const QModelIndex &parent) const {
    if (parent.isValid()) {
        return 0;
    }
    return m_entries.size();
}

QVariant WatchModel::data(const QModelIndex &index, int role) const {
    if (!index.isValid() || index.row() < 0 || index.row() >= m_entries.size()) {
        return {};
    }
    const Entry &e = m_entries.at(index.row());
    switch (role) {
    case ExprRole:         return e.expr;
    case AddressHexRole:   return e.addressHex;
    case TypeNameRole:     return e.typeName;
    case KindRole:         return e.kind;
    case DisplayRole:      return e.display;
    case HexRole:          return e.hex;
    case ErrorRole:        return e.error;
    case ChangedRole:      return e.changed;
    case ChangeSerialRole: return e.changeSerial;
    default:               return {};
    }
}

QHash<int, QByteArray> WatchModel::roleNames() const {
    return {
        {ExprRole,         "expr"},
        {AddressHexRole,   "addressHex"},
        {TypeNameRole,     "typeName"},
        {KindRole,         "kind"},
        {DisplayRole,      "display"},
        {HexRole,          "hex"},
        {ErrorRole,        "error"},
        {ChangedRole,      "changed"},
        {ChangeSerialRole, "changeSerial"},
    };
}

void WatchModel::clear() {
    if (m_entries.isEmpty()) {
        return;
    }
    beginResetModel();
    m_entries.clear();
    endResetModel();
    emit countChanged();
}

QStringList WatchModel::expressions() const {
    QStringList out;
    out.reserve(m_entries.size());
    for (const Entry &e : m_entries) {
        out.append(e.expr);
    }
    return out;
}

void WatchModel::updateFromSnapshot(const QJsonObject &watchObj, const QStringList &exprs) {
    QHash<QString, Entry> prevByExpr;
    for (const Entry &e : m_entries) {
        prevByExpr.insert(e.expr, e);
    }

    QStringList ordered = exprs;
    const QStringList keys = watchObj.keys();
    for (const QString &k : keys) {
        if (!ordered.contains(k)) {
            ordered.append(k);
        }
    }

    QList<Entry> next;
    next.reserve(ordered.size());

    for (const QString &expr : ordered) {
        const QJsonObject valObj = watchObj.value(expr).toObject();
        Entry e;
        e.expr = expr;
        e.addressHex = valObj.value(QStringLiteral("address_hex")).toString();
        e.typeName = valObj.value(QStringLiteral("type_name")).toString();
        e.kind = valObj.value(QStringLiteral("kind")).toString();
        e.error = valObj.value(QStringLiteral("error")).toString();
        e.hex = valObj.value(QStringLiteral("hex")).toString();

        if (valObj.contains(QStringLiteral("display")) && !valObj.value(QStringLiteral("display")).isNull()) {
            e.display = valObj.value(QStringLiteral("display")).toString();
        } else if (valObj.contains(QStringLiteral("value"))) {
            e.display = compactScalar(valObj.value(QStringLiteral("value")));
        } else {
            e.display = e.error.isEmpty() ? QStringLiteral("-") : QStringLiteral("<error>");
        }

        auto prevIt = prevByExpr.constFind(expr);
        if (prevIt != prevByExpr.constEnd()) {
            e.changeSerial = prevIt.value().changeSerial;
            const bool valueChanged = (prevIt.value().display != e.display) || (prevIt.value().hex != e.hex);
            if (valueChanged) {
                e.changed = true;
                ++e.changeSerial;
            }
        }
        next.append(std::move(e));
    }

    beginResetModel();
    m_entries = std::move(next);
    endResetModel();
    emit countChanged();
}
