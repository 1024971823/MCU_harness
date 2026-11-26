pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import McuStudio
import "components"
import "shell"
import "pages"
import "plugins"
import "dialogs"

ApplicationWindow {
    id: win
    visible: true
    width: 1440
    height: 900
    minimumWidth: 1100
    minimumHeight: 700
    color: Theme.bgCanvas

    readonly property var targetObj: Workbench.target || ({})
    readonly property string projectBasename: targetObj.projectDir ? Utils.basename(targetObj.projectDir) : ""
    title: qsTr("MCU Harness Studio") + (projectBasename.length > 0 ? " — " + projectBasename : "")

    property int activePage: 0
    property bool threadsVisible: true
    property bool pluginPanelVisible: false
    property string activePlugin: "probe"
    property bool pluginMaximized: false
    property int _preMaximizePage: 0

    // Expose dialog/page references for ScreenshotDriver & Shortcuts
    readonly property alias approvalDialog: approvalDlg
    readonly property alias commandPalette: cmdPalette
    readonly property alias onboardingWizard: onboardingDlg
    readonly property alias licenseDialog: licenseDlg
    readonly property alias updateDialog: updateDlg
    readonly property alias crashReportDialog: crashDlg
    readonly property alias aboutDialog: aboutDlg
    readonly property alias settingsPage: settingsPg

    onThreadsVisibleChanged: {
        if (Settings.threadsPanelVisible !== threadsVisible) {
            Settings.setThreadsPanelVisible(threadsVisible)
        }
    }

    onPluginPanelVisibleChanged: {
        if (Settings.pluginPanelVisible !== pluginPanelVisible) {
            Settings.setPluginPanelVisible(pluginPanelVisible)
        }
    }

    onActivePluginChanged: {
        if (Settings.activePlugin !== activePlugin) {
            Settings.setActivePlugin(activePlugin)
        }
    }

    function showToast(msg, level, actionText, cb) {
        toastHost.show(msg, level || "info", actionText || "", cb)
    }

    function openPage(idx) {
        if (idx !== 5) {
            pluginMaximized = false
        }
        activePage = idx
    }

    function firstEnabledPluginId() {
        var plugs = PluginRegistry.plugins
        for (var i = 0; i < plugs.length; ++i) {
            if (PluginRegistry.isEnabled(plugs[i].id)) return plugs[i].id
        }
        return "probe"
    }

    function openPlugin(id) {
        var targetId = (id && PluginRegistry.byId(id)) ? id : firstEnabledPluginId()
        activePlugin = targetId
        Settings.setActivePlugin(targetId)
        if (pluginMaximized) {
            pluginMaximized = false
            activePage = (_preMaximizePage >= 0 && _preMaximizePage <= 4) ? _preMaximizePage : 0
        }
        pluginPanelVisible = true
        Settings.setPluginPanelVisible(true)
    }

    function togglePluginPanel() {
        if (pluginMaximized) {
            restorePlugin()
            pluginPanelVisible = false
            return
        }
        if (!pluginPanelVisible) {
            if (!activePlugin || !PluginRegistry.byId(activePlugin)) {
                activePlugin = firstEnabledPluginId()
            }
            pluginPanelVisible = true
        } else {
            pluginPanelVisible = false
        }
    }

    function handlePluginBarClick(id) {
        if (pluginMaximized) {
            if (activePlugin === id) {
                restorePlugin()
            } else {
                activePlugin = id
            }
            return
        }
        if (pluginPanelVisible && activePlugin === id) {
            pluginPanelVisible = false
        } else {
            openPlugin(id)
        }
    }

    function maximizePlugin() {
        if (activePage !== 5) {
            _preMaximizePage = activePage
        }
        if (!activePlugin || !PluginRegistry.byId(activePlugin)) {
            activePlugin = firstEnabledPluginId()
        }
        pluginMaximized = true
        activePage = 5
    }

    function restorePlugin() {
        pluginMaximized = false
        activePage = (_preMaximizePage >= 0 && _preMaximizePage <= 4) ? _preMaximizePage : 0
    }

    Component.onCompleted: {
        if (!AppInfo.screenshotMode) {
            if (Settings.threadsPanelVisible !== undefined) threadsVisible = Settings.threadsPanelVisible
            if (Settings.pluginPanelVisible !== undefined) pluginPanelVisible = Settings.pluginPanelVisible
            if (Settings.activePlugin && Settings.activePlugin.length > 0) activePlugin = Settings.activePlugin
            if (Settings.firstRunCompleted === false) {
                onboardingDlg.open()
            } else if (Diagnostics.hasCrashReport) {
                crashDlg.open()
            }
        }
    }

    Connections {
        target: Workbench
        function onPendingApprovalsCountChanged() {
            if (!AppInfo.screenshotMode && (Workbench.pendingApprovalsCount || 0) > 0 && !approvalDlg.opened) {
                approvalDlg.demoOverride = null
                approvalDlg.open()
            }
        }
    }

    // Global Application Shortcuts
    Shortcut {
        sequences: ["Ctrl+K", "Ctrl+Shift+P"]
        context: Qt.ApplicationShortcut
        onActivated: cmdPalette.openPalette()
    }
    Shortcut {
        sequence: "Ctrl+B"
        context: Qt.ApplicationShortcut
        onActivated: win.threadsVisible = !win.threadsVisible
    }
    Shortcut {
        sequence: "Ctrl+J"
        context: Qt.ApplicationShortcut
        onActivated: win.togglePluginPanel()
    }
    Shortcut {
        sequence: "Ctrl+,"
        context: Qt.ApplicationShortcut
        onActivated: win.openPage(4)
    }
    Shortcut {
        sequence: "Ctrl+1"
        context: Qt.ApplicationShortcut
        onActivated: win.openPage(0)
    }
    Shortcut {
        sequence: "Ctrl+2"
        context: Qt.ApplicationShortcut
        onActivated: win.openPage(1)
    }
    Shortcut {
        sequence: "Ctrl+3"
        context: Qt.ApplicationShortcut
        onActivated: win.openPage(2)
    }
    Shortcut {
        sequence: "Ctrl+4"
        context: Qt.ApplicationShortcut
        onActivated: win.openPage(3)
    }
    Shortcut {
        sequence: "Ctrl+L"
        context: Qt.ApplicationShortcut
        onActivated: {
            win.openPage(0)
            chatPg.focusComposer()
        }
    }
    Shortcut {
        sequence: "Ctrl+N"
        context: Qt.ApplicationShortcut
        onActivated: {
            Workbench.newSession()
            win.openPage(0)
            win.showToast(qsTr("Started a new chat"), "info")
        }
    }
    Shortcut {
        sequence: "Ctrl+O"
        context: Qt.ApplicationShortcut
        onActivated: projectFolderDlg.open()
    }
    Shortcut {
        sequence: "F5"
        context: Qt.ApplicationShortcut
        onActivated: {
            Workbench.refreshSnapshot()
            win.showToast(qsTr("Telemetry refreshed"), "info")
        }
    }
    Shortcut {
        sequence: "Escape"
        context: Qt.ApplicationShortcut
        onActivated: {
            if (cmdPalette.opened) cmdPalette.close()
            else if (aboutDlg.opened) aboutDlg.close()
            else if (licenseDlg.opened) licenseDlg.close()
            else if (updateDlg.opened) updateDlg.close()
            else if (win.pluginMaximized) win.restorePlugin()
            else if (Workbench.turnRunning) Workbench.abortTurn()
        }
    }

    FolderDialog {
        id: projectFolderDlg
        title: qsTr("Open Embedded Project Directory")
        onAccepted: {
            Workbench.openProject(selectedFolder)
            win.showToast(qsTr("Opened project: %1").arg(Utils.basename(selectedFolder)), "success")
        }
    }

    // Internal components for full-screen maximized plugin page (StackLayout index 5)
    Component { id: maxProbeComp; ProbePlugin {} }
    Component { id: maxWatchComp; WatchPlugin {} }
    Component { id: maxRtosComp; RtosPlugin {} }
    Component { id: maxConsoleComp; ConsolePlugin {} }
    Component { id: maxAuditComp; AuditPlugin {} }
    Component { id: maxEngineComp; EngineLogPlugin {} }

    function maxComponentForPlugin(id) {
        switch (id) {
        case "probe":   return maxProbeComp
        case "watch":   return maxWatchComp
        case "rtos":    return maxRtosComp
        case "console": return maxConsoleComp
        case "audit":   return maxAuditComp
        case "engine":  return maxEngineComp
        default:        return maxProbeComp
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            NavRail {
                Layout.fillHeight: true
                activePage: win.activePage
                pendingApprovalsCount: Workbench.pendingApprovalsCount || 0
                onPageSelected: function(idx) { win.openPage(idx) }
                onOpenLicenseRequested: licenseDlg.open()
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                TopBar {
                    Layout.fillWidth: true
                    activePage: win.activePage
                    threadsVisible: win.threadsVisible
                    pluginPanelVisible: win.pluginPanelVisible && !win.pluginMaximized
                    onOpenProjectRequested: projectFolderDlg.open()
                    onToggleThreadsRequested: win.threadsVisible = !win.threadsVisible
                    onTogglePluginPanelRequested: win.togglePluginPanel()
                    onOpenCommandPaletteRequested: cmdPalette.openPalette()
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 0

                    ThreadsPanel {
                        Layout.fillHeight: true
                        visible: win.activePage === 0 && win.threadsVisible
                        onNewChatRequested: {
                            Workbench.newSession()
                            win.openPage(0)
                            win.showToast(qsTr("Started a new chat"), "info")
                        }
                        onLoadSessionRequested: function(sid) {
                            Workbench.loadSession(sid)
                            win.openPage(0)
                        }
                        onOpenProbeRequested: win.openPlugin("probe")
                    }

                    StackLayout {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        currentIndex: win.activePage

                        // 0: Chat
                        ChatPage {
                            id: chatPg
                            onOpenSettingsRequested: function(section) {
                                win.openPage(4)
                                if (section) settingsPg.activeSection = section
                            }
                            onOpenApprovalRequested: {
                                approvalDlg.demoOverride = null
                                approvalDlg.open()
                            }
                            onOpenPluginRequested: function(pluginId) {
                                win.openPlugin(pluginId)
                            }
                        }

                        // 1: Skills
                        SkillsPage {
                            onRunSkillRequested: function(cmd) {
                                win.openPage(0)
                                chatPg.insertSkill(cmd)
                                chatPg.focusComposer()
                            }
                        }

                        // 2: Plugins
                        PluginsPage {
                            onOpenPluginRequested: function(pluginId) {
                                win.openPlugin(pluginId)
                            }
                        }

                        // 3: History
                        HistoryPage {
                            onSessionOpened: win.openPage(0)
                        }

                        // 4: Settings
                        SettingsPage {
                            id: settingsPg
                            onOpenLicenseRequested: licenseDlg.open()
                            onOpenUpdateRequested: updateDlg.open()
                            onOpenAboutRequested: aboutDlg.open()
                            onOpenToast: function(msg, lvl) { win.showToast(msg, lvl) }
                        }

                        // 5: Maximized Plugin View
                        Item {
                            id: maximizedPluginPage
                            readonly property var maxMeta: PluginRegistry.byId(win.activePlugin) || PluginRegistry.plugins[0]

                            ColumnLayout {
                                anchors.fill: parent
                                spacing: 0

                                PanelHeader {
                                    Layout.fillWidth: true
                                    icon: maximizedPluginPage.maxMeta ? maximizedPluginPage.maxMeta.icon : Icons.cpu
                                    hue: maximizedPluginPage.maxMeta ? maximizedPluginPage.maxMeta.hue : Theme.hueAmber
                                    title: maximizedPluginPage.maxMeta ? maximizedPluginPage.maxMeta.name : qsTr("Plugin")
                                    maximizable: true
                                    maximized: true
                                    closable: true
                                    onMaximizeRequested: win.restorePlugin()
                                    onCloseRequested: {
                                        win.restorePlugin()
                                        win.pluginPanelVisible = false
                                    }
                                }

                                Loader {
                                    id: maxPluginLoader
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    active: win.activePage === 5 && win.pluginMaximized && win.activePlugin.length > 0
                                    sourceComponent: win.maxComponentForPlugin(win.activePlugin)
                                }
                            }

                            Connections {
                                target: maxPluginLoader.item
                                ignoreUnknownSignals: true
                                function onToastRequested(msg, level) {
                                    win.showToast(msg, level)
                                }
                            }
                        }
                    }

                    PluginPanel {
                        Layout.fillHeight: true
                        activePlugin: win.activePlugin
                        panelOpen: win.pluginPanelVisible && !win.pluginMaximized && win.activePage !== 5
                        onMaximizeRequested: win.maximizePlugin()
                        onCloseRequested: win.pluginPanelVisible = false
                        onToastRequested: function(msg, level) { win.showToast(msg, level) }
                    }
                }
            }

            PluginBar {
                Layout.fillHeight: true
                activePlugin: win.activePlugin
                pluginPanelVisible: win.pluginPanelVisible || win.pluginMaximized
                onPluginClicked: function(pluginId) { win.handlePluginBarClick(pluginId) }
                onOpenPluginsPageRequested: win.openPage(2)
            }
        }

        StatusBar {
            Layout.fillWidth: true
            onOpenPluginRequested: function(pluginId) { win.openPlugin(pluginId) }
            onOpenSettingsSection: function(section) {
                win.openPage(4)
                if (section) settingsPg.activeSection = section
            }
            onOpenApprovalDialog: {
                approvalDlg.demoOverride = null
                approvalDlg.open()
            }
            onOpenLicenseDialog: licenseDlg.open()
            onOpenUpdateDialog: updateDlg.open()
        }
    }

    ToastHost {
        id: toastHost
        anchors.fill: parent
        anchors.bottomMargin: Theme.statusBarHeight
    }

    // Dialogs & Overlays
    ApprovalDialog {
        id: approvalDlg
        onToastRequested: function(msg, lvl) { win.showToast(msg, lvl) }
    }

    CommandPalette {
        id: cmdPalette
        onExecuteAction: function(actionId, payload) {
            if (actionId === "nav") win.openPage(payload)
            else if (actionId === "openPlugin") win.openPlugin(payload)
            else if (actionId === "toggleThreads") win.threadsVisible = !win.threadsVisible
            else if (actionId === "togglePluginPanel") win.togglePluginPanel()
            else if (actionId === "newSession") { Workbench.newSession(); win.openPage(0) }
            else if (actionId === "openProject") projectFolderDlg.open()
            else if (actionId === "refresh") Workbench.refreshSnapshot()
            else if (actionId === "theme") Settings.setTheme(Theme.isDark ? "light" : "dark")
            else if (actionId === "language") Settings.setLanguage(Settings.language === "zh_CN" ? "en" : "zh_CN")
            else if (actionId === "license") licenseDlg.open()
            else if (actionId === "updates") updateDlg.open()
            else if (actionId === "about") aboutDlg.open()
            else if (actionId === "playbook") {
                win.openPage(0)
                chatPg.insertSkill(payload + " ")
                chatPg.focusComposer()
            }
            else if (actionId === "probeConnect") Workbench.probeConnect(Settings.probeBackend || "jlink")
            else if (actionId === "probeDisconnect") Workbench.probeDisconnect()
            else if (actionId === "probeHalt") Workbench.probeHalt()
            else if (actionId === "probeResume") Workbench.probeResume()
        }
    }

    OnboardingWizard {
        id: onboardingDlg
        onCompleted: win.showToast(qsTr("Workspace ready!"), "success")
    }

    LicenseDialog {
        id: licenseDlg
        onToastRequested: function(msg, lvl) { win.showToast(msg, lvl) }
    }

    UpdateDialog {
        id: updateDlg
        onToastRequested: function(msg, lvl) { win.showToast(msg, lvl) }
    }

    CrashReportDialog {
        id: crashDlg
        onToastRequested: function(msg, lvl) { win.showToast(msg, lvl) }
    }

    AboutDialog {
        id: aboutDlg
        onToastRequested: function(msg, lvl) { win.showToast(msg, lvl) }
    }

    Loader {
        id: screenshotDriverLoader
        objectName: "screenshotDriver"
        active: AppInfo.screenshotMode
        source: "shell/ScreenshotDriver.qml"
        onLoaded: {
            if (item) item.mainWindow = win
        }

        // Proxy gotoState directly on the Loader so findChild("screenshotDriver") works either way!
        function gotoState(state) {
            var drv = item as ScreenshotDriver
            if (drv) {
                drv.gotoState(state)
            }
        }
    }
}
