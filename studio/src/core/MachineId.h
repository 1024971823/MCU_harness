#pragma once

#include <QString>

class MachineId {
public:
    static QString current();
    static QString normalizeHash(const QString &raw);
};
