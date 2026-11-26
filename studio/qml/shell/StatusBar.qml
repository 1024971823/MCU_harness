import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Rectangle {
    id: root
    implicitHeight: Theme.statusBarHeight
    color: Theme.bgSidebar

    signal navigateToPage(int index)
    signal openApprovalDialog()
    signal openLicenseDialog()
    signal openUpdateDialog()

    readonly property var probeObj: Workbench.probe || ({})
    readonly property string modeName: Workbench.interlockMode || "interactive"

    Divider {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 1
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.s3
        anchors.rightMargin: Theme.s3
        spacing: Theme.s3

        // Engine status
        RowLayout {
            spacing: Theme.s1 + 2

            StatusDot {
                size: 7
                color: {
                    if (Workbench.engineStatus === "ready") return Theme.success
                    if (Workbench.engineStatus === "error") return Theme.danger
                    return Theme.warning
                }
                pulsing: Workbench.turnRunning || Workbench.engineStatus === "starting"
            }

            Text {
                text: {
                    if (Workbench.turnRunning) return qsTr("Agent Running")
                    if (Workbench.engineStatus === "ready") return qsTr("Engine Ready")
                    if (Workbench.engineStatus === "error") return qsTr("Engine Error")
                    return qsTr("Starting Engine...")
                }
                font.family: Theme.fontSans
                font.pixelSize: Theme.textXs
                font.weight: Theme.weightMedium
                color: Theme.textSecondary
            }
        }

        Divider { orientation: Qt.Vertical; Layout.preferredHeight: 12 }

        // Probe status
        Item {
            Layout.preferredHeight: 20
            implicitWidth: probeRow.implicitWidth + Theme.s2

            HoverHandler { id: probeHover }
            TapHandler { onTapped: root.navigateToPage(1) }

            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSm
                color: probeHover.hovered ? Theme.bgHover : "transparent"
            }

            RowLayout {
                id: probeRow
                anchors.centerIn: parent
                spacing: Theme.s1 + 2

                Icon {
                    name: root.probeObj.connected ? Icons.plugZap : Icons.unplug
                    size: 12
                    color: root.probeObj.connected ? Theme.success : Theme.textMuted
                }

                Text {
                    text: {
                        if (!root.probeObj.connected) return qsTr("Probe: Disconnected")
                        var dev = root.probeObj.device || "Target"
                        var st = root.probeObj.halted ? qsTr("Halted") : qsTr("Running")
                        return dev + " (" + st + ")"
                    }
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textXs
                    color: root.probeObj.connected ? Theme.textPrimary : Theme.textMuted
                }
            }
        }

        Divider { orientation: Qt.Vertical; Layout.preferredHeight: 12 }

        // Interlock mode pill
        Item {
            Layout.preferredHeight: 20
            implicitWidth: modeRow.implicitWidth + Theme.s2

            HoverHandler { id: modeHover }
            TapHandler { onTapped: root.navigateToPage(6) }

            Rectangle {
                anchors.fill: parent
                radius: Theme.radiusSm
                color: modeHover.hovered ? Theme.bgHover : "transparent"
            }

            RowLayout {
                id: modeRow
                anchors.centerIn: parent
                spacing: Theme.s1

                Icon {
                    name: root.modeName === "read_only" ? Icons.lock : (root.modeName === "auto_safe" ? Icons.shieldCheck : Icons.shieldAlert)
                    size: 12
                    color: root.modeName === "read_only" ? Theme.info : (root.modeName === "auto_safe" ? Theme.success : Theme.warning)
                }

                Text {
                    readonly property string localizedMode: {
                        if (root.modeName === "auto_safe") return qsTr("Auto-Safe")
                        if (root.modeName === "read_only") return qsTr("Read-Only")
                        return qsTr("Interactive")
                    }
                    text: qsTr("Interlock: %1").arg(localizedMode)
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textXs
                    color: Theme.textSecondary
                }
            }
        }

        Item { Layout.fillWidth: true }

        // Pending Approvals indicator
        Badge {
            visible: (Workbench.pendingApprovalsCount || 0) > 0
            text: qsTr("%1 Approval Pending").arg(Workbench.pendingApprovalsCount || 0)
            variant: "danger"
            iconName: Icons.shieldAlert
            compact: true

            TapHandler { onTapped: root.openApprovalDialog() }
        }

        // Token summary
        Text {
            visible: (Workbench.tokenUsageText || "").length > 0
            text: Workbench.tokenUsageText || ""
            font.family: Theme.fontMono
            font.pixelSize: Theme.textXs
            color: Theme.textMuted
        }

        // Update available pill
        Badge {
            visible: Updater.updateAvailable
            text: qsTr("Update %1").arg(Updater.latestVersion || "")
            variant: "accent"
            iconName: Icons.download
            compact: true

            TapHandler { onTapped: root.openUpdateDialog() }
        }

        // License pill
        Badge {
            text: {
                if (License.status === "active") return License.tier || qsTr("Pro")
                if (License.status === "expired") return qsTr("License Expired")
                return qsTr("Trial · %1d").arg(License.trialDaysRemaining || 0)
            }
            variant: License.status === "active" ? "success" : (License.status === "expired" ? "danger" : "neutral")
            iconName: Icons.keyRound
            compact: true

            TapHandler { onTapped: root.openLicenseDialog() }
        }

        // Language quick toggle
        IconButton {
            buttonSize: 20
            iconSize: 12
            iconName: Icons.languages
            tooltipText: Settings.language === "zh_CN" ? qsTr("Switch to English") : qsTr("Switch to 中文")
            onClicked: Settings.setLanguage(Settings.language === "zh_CN" ? "en" : "zh_CN")
        }

        // Theme quick toggle
        IconButton {
            buttonSize: 20
            iconSize: 12
            iconName: Theme.isDark ? Icons.moon : Icons.sun
            tooltipText: Theme.isDark ? qsTr("Switch to Light Theme") : qsTr("Switch to Dark Theme")
            onClicked: Settings.setTheme(Theme.isDark ? "light" : "dark")
        }
    }
}
