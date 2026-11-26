pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Rectangle {
    id: root
    implicitHeight: Theme.statusBarHeight
    color: Theme.bgSidebar

    signal openPluginRequested(string pluginId)
    signal openSettingsSection(string section)
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
        spacing: Theme.s2 + 2

        // Engine status dot + label
        RowLayout {
            spacing: Theme.s1 + 2

            StatusDot {
                size: 7
                color: {
                    if (Workbench.engineStatus === "ready") return Theme.hueEmerald
                    if (Workbench.engineStatus === "error") return Theme.hueRose
                    return Theme.hueAmber
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

        // Provider / model pill
        Pill {
            compact: true
            clickable: true
            hue: Theme.hueIndigo
            icon: Icons.sparkles
            text: (Workbench.provider || Settings.provider || "deepseek") + " · " + (Workbench.model || Settings.model || "deepseek-chat")
            tooltipText: qsTr("Configure AI Model & Provider")
            onClicked: root.openSettingsSection("model")
        }

        // Probe state (hueAmber chip)
        Pill {
            compact: true
            clickable: true
            hue: root.probeObj.connected ? Theme.hueAmber : Theme.hueSlate
            icon: root.probeObj.connected ? Icons.cpu : Icons.unplug
            text: {
                if (!root.probeObj.connected) return qsTr("Probe: Disconnected")
                var dev = root.probeObj.device || "Target"
                var st = root.probeObj.halted ? qsTr("Halted") : qsTr("Running")
                return dev + " · " + st
            }
            tooltipText: qsTr("Open Probe & Target Plugin")
            onClicked: root.openPluginRequested("probe")
        }

        // Interlock mode (read_only emerald, interactive amber, auto_safe rose)
        Pill {
            compact: true
            clickable: true
            hue: root.modeName === "read_only"
                ? Theme.hueEmerald
                : (root.modeName === "auto_safe" ? Theme.hueRose : Theme.hueAmber)
            icon: root.modeName === "read_only"
                ? Icons.lock
                : (root.modeName === "auto_safe" ? Icons.shieldCheck : Icons.shieldAlert)
            text: {
                if (root.modeName === "auto_safe") return qsTr("Auto-Safe")
                if (root.modeName === "read_only") return qsTr("Read-Only")
                return qsTr("Interactive")
            }
            tooltipText: qsTr("Hardware Safety Interlock Settings")
            onClicked: root.openSettingsSection("safety")
        }

        Item { Layout.fillWidth: true }

        // Pending Approvals amber pill when > 0
        Pill {
            visible: (Workbench.pendingApprovalsCount || 0) > 0
            compact: true
            clickable: true
            hue: Theme.hueAmber
            icon: Icons.shieldAlert
            text: qsTr("%1 Approval Pending").arg(Workbench.pendingApprovalsCount || 0)
            tooltipText: qsTr("Review pending hardware approval")
            onClicked: root.openApprovalDialog()
        }

        // Tokens counter
        Text {
            visible: (Workbench.tokenUsageText || "").length > 0
            text: Workbench.tokenUsageText || ""
            font.family: Theme.fontMono
            font.pixelSize: Theme.textXs
            color: Theme.textMuted
        }

        // Update available pill
        Pill {
            visible: Updater.updateAvailable
            compact: true
            clickable: true
            hue: Theme.hueSky
            icon: Icons.download
            text: qsTr("Update %1").arg(Updater.latestVersion || "")
            tooltipText: qsTr("View available update")
            onClicked: root.openUpdateDialog()
        }

        // License tier / trial chip
        Pill {
            compact: true
            clickable: true
            icon: Icons.keyRound
            hue: License.status === "active"
                ? Theme.hueEmerald
                : (License.status === "expired" ? Theme.hueRose : Theme.hueViolet)
            text: {
                if (License.status === "active") return License.tier || qsTr("Pro")
                if (License.status === "expired") return qsTr("Expired")
                return qsTr("Trial · %1d").arg(License.trialDaysRemaining || 0)
            }
            tooltipText: qsTr("Manage License")
            onClicked: root.openLicenseDialog()
        }

        Divider { orientation: Qt.Vertical; Layout.preferredHeight: 12 }

        // Version
        Text {
            text: "v" + (AppInfo.version || "1.0.0")
            font.family: Theme.fontMono
            font.pixelSize: Theme.textXs
            color: Theme.textMuted
        }
    }
}
