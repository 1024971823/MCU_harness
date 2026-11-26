import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import McuStudio
import "components"
import "shell"
import "pages"
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
    property bool sidebarVisible: Settings.sidebarVisible !== undefined ? Settings.sidebarVisible : true
    property bool bottomPanelVisible: Settings.bottomPanelVisible !== undefined ? Settings.bottomPanelVisible : false

    // Expose dialog/page references for ScreenshotDriver & Shortcuts
    readonly property alias approvalDialog: approvalDlg
    readonly property alias commandPalette: cmdPalette
    readonly property alias onboardingWizard: onboardingDlg
    readonly property alias licenseDialog: licenseDlg
    readonly property alias updateDialog: updateDlg
    readonly property alias crashReportDialog: crashDlg
    readonly property alias aboutDialog: aboutDlg
    readonly property alias settingsPage: settingsPg

    function showToast(msg, level, actionText, cb) {
        toastHost.show(msg, level || "info", actionText || "", cb)
    }

    function openPage(idx) {
        activePage = idx
    }

    Component.onCompleted: {
        if (!AppInfo.screenshotMode) {
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
        onActivated: win.sidebarVisible = !win.sidebarVisible
    }
    Shortcut {
        sequence: "Ctrl+J"
        context: Qt.ApplicationShortcut
        onActivated: win.bottomPanelVisible = !win.bottomPanelVisible
    }
    Shortcut {
        sequence: "Ctrl+,"
        context: Qt.ApplicationShortcut
        onActivated: win.activePage = 6
    }
    Shortcut {
        sequence: "Ctrl+1"
        context: Qt.ApplicationShortcut
        onActivated: win.activePage = 0
    }
    Shortcut {
        sequence: "Ctrl+2"
        context: Qt.ApplicationShortcut
        onActivated: win.activePage = 1
    }
    Shortcut {
        sequence: "Ctrl+3"
        context: Qt.ApplicationShortcut
        onActivated: win.activePage = 2
    }
    Shortcut {
        sequence: "Ctrl+4"
        context: Qt.ApplicationShortcut
        onActivated: win.activePage = 3
    }
    Shortcut {
        sequence: "Ctrl+5"
        context: Qt.ApplicationShortcut
        onActivated: win.activePage = 4
    }
    Shortcut {
        sequence: "Ctrl+6"
        context: Qt.ApplicationShortcut
        onActivated: win.activePage = 5
    }
    Shortcut {
        sequence: "Ctrl+L"
        context: Qt.ApplicationShortcut
        onActivated: {
            win.activePage = 0
            agentPg.focusComposer()
        }
    }
    Shortcut {
        sequence: "Ctrl+N"
        context: Qt.ApplicationShortcut
        onActivated: {
            Workbench.newSession()
            win.activePage = 0
            win.showToast(qsTr("Started a new Agent session"), "info")
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

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            ActivityBar {
                Layout.fillHeight: true
                activePage: win.activePage
                sidebarVisible: win.sidebarVisible
                bottomPanelVisible: win.bottomPanelVisible
                pendingApprovalsCount: Workbench.pendingApprovalsCount || 0
                onPageSelected: function(idx) { win.activePage = idx }
                onToggleSidebar: win.sidebarVisible = !win.sidebarVisible
                onToggleBottomPanel: win.bottomPanelVisible = !win.bottomPanelVisible
                onOpenCommandPalette: cmdPalette.openPalette()
            }

            SplitView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                orientation: Qt.Horizontal

                handle: Rectangle {
                    implicitWidth: 3
                    color: SplitHandle.hovered ? Theme.accent : "transparent"
                }

                SideBar {
                    visible: win.sidebarVisible
                    SplitView.preferredWidth: 256
                    SplitView.minimumWidth: 200
                    SplitView.maximumWidth: 380
                    SplitView.fillHeight: true
                    onOpenProjectRequested: projectFolderDlg.open()
                    onLaunchPlaybook: function(cmdText) {
                        win.activePage = 0
                        agentPg.insertPlaybook(cmdText)
                    }
                    onLoadSessionRequested: function(sid) {
                        Workbench.loadSession(sid)
                        win.activePage = 0
                    }
                    onNavigateToPage: function(idx) { win.activePage = idx }
                    onCollapseRequested: win.sidebarVisible = false
                }

                SplitView {
                    SplitView.fillWidth: true
                    SplitView.fillHeight: true
                    orientation: Qt.Vertical

                    handle: Rectangle {
                        implicitHeight: 3
                        color: SplitHandle.hovered ? Theme.accent : "transparent"
                    }

                    StackLayout {
                        SplitView.fillWidth: true
                        SplitView.fillHeight: true
                        currentIndex: win.activePage

                        AgentPage {
                            id: agentPg
                            onOpenSettingsRequested: function(section) {
                                win.activePage = 6
                                if (section) settingsPg.activeSection = section
                            }
                            onOpenApprovalRequested: {
                                approvalDlg.demoOverride = null
                                approvalDlg.open()
                            }
                            onOpenLicenseRequested: licenseDlg.open()
                        }
                        HardwarePage {
                            onOpenToast: function(msg, lvl) { win.showToast(msg, lvl) }
                        }
                        WatchPage {}
                        RtosPage {}
                        SessionsPage {
                            onSessionOpened: win.activePage = 0
                        }
                        AuditPage {}
                        SettingsPage {
                            id: settingsPg
                            onOpenLicenseRequested: licenseDlg.open()
                            onOpenUpdateRequested: updateDlg.open()
                            onOpenAboutRequested: aboutDlg.open()
                            onOpenToast: function(msg, lvl) { win.showToast(msg, lvl) }
                        }
                    }

                    BottomPanel {
                        visible: win.bottomPanelVisible
                        SplitView.preferredHeight: 230
                        SplitView.minimumHeight: 140
                        SplitView.maximumHeight: 480
                        SplitView.fillWidth: true
                        onCloseRequested: win.bottomPanelVisible = false
                    }
                }
            }
        }

        StatusBar {
            Layout.fillWidth: true
            onNavigateToPage: function(idx) { win.activePage = idx }
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
            if (actionId === "nav") win.activePage = payload
            else if (actionId === "toggleSidebar") win.sidebarVisible = !win.sidebarVisible
            else if (actionId === "toggleBottom") win.bottomPanelVisible = !win.bottomPanelVisible
            else if (actionId === "newSession") { Workbench.newSession(); win.activePage = 0 }
            else if (actionId === "openProject") projectFolderDlg.open()
            else if (actionId === "refresh") Workbench.refreshSnapshot()
            else if (actionId === "theme") Settings.setTheme(Theme.isDark ? "light" : "dark")
            else if (actionId === "language") Settings.setLanguage(Settings.language === "zh_CN" ? "en" : "zh_CN")
            else if (actionId === "license") licenseDlg.open()
            else if (actionId === "updates") updateDlg.open()
            else if (actionId === "about") aboutDlg.open()
            else if (actionId === "playbook") { win.activePage = 0; agentPg.insertPlaybook(payload + " ") }
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
