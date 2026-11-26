#pragma once

#include <QString>
#include <QStringList>

class Logger {
public:
    static void install();
    static void uninstall();
    static void setVerbose(bool verbose);
    static void setEchoToStderr(bool echo);
    static QString logDirectory();
    static QString currentLogFile();
    static QStringList recentLogFiles(int maxCount = 3);
};
