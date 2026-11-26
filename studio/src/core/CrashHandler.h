#pragma once

#include <QString>

class CrashHandler {
public:
    static void install(const QString &version, const QString &gitHash);
    static QString crashDirectory();
};
