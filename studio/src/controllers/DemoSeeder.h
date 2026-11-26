#pragma once

#include <QString>

class DiagnosticsService;
class UpdateManager;
class WorkbenchController;

class DemoSeeder {
public:
    static void seed(WorkbenchController *wb,
                     UpdateManager *updater = nullptr,
                     DiagnosticsService *diagnostics = nullptr,
                     const QString &state = QString());
};
