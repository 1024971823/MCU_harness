import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

AppDialog {
    id: root
    property var demoOverride: null
    signal toastRequested(string message, string level)

    readonly property var req: demoOverride ? demoOverride : (Workbench.currentApproval || ({}))
    readonly property string reqId: req.requestId || ""
    readonly property string toolName: req.toolName || "unknown_tool"
    readonly property string riskLevel: (req.riskLevel || "hardware_state").toLowerCase()
    readonly property bool isDanger: riskLevel === "hardware_danger" || req.isHardwareDanger === true
    readonly property string diffPreview: req.diffPreview || ""
    readonly property string svdPreview: req.svdPreview || ""
    readonly property string detailsJson: req.detailsJson || "{}"
    readonly property int queueTotal: demoOverride ? 1 : Math.max(1, Workbench.pendingApprovalsCount || 1)

    property string activeTab: diffPreview.length > 0 ? "changes" : (svdPreview.length > 0 ? "changes" : "args")

    dialogWidth: 640
    critical: true
    iconName: Icons.shieldAlert
    iconColor: isDanger ? Theme.danger : (riskLevel === "hardware_state" ? Theme.warning : (riskLevel === "shell" ? Theme.purple : Theme.info))
    stripColor: iconColor
    title: qsTr("Hardware Safety Approval Required")
    subtitle: qsTr("HITL Interlock paused tool execution pending your explicit review")

    onOpened: {
        reasonField.text = ""
        activeTab = (diffPreview.length > 0 || svdPreview.length > 0) ? "changes" : "args"
    }

    function submitDecision(approved, scope) {
        var r = reasonField.text.trim()
        if (demoOverride) {
            root.toastRequested(approved ? qsTr("Demo approval accepted (%1)").arg(scope) : qsTr("Demo approval denied"), approved ? "success" : "warning")
            demoOverride = null
            root.close()
            return
        }
        Workbench.resolveApproval(root.reqId, approved, scope, r)
        if ((Workbench.pendingApprovalsCount || 0) <= 1) {
            root.close()
        }
    }

    // Tool & Queue Header Bar
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.s2

        Text {
            text: root.toolName
            font.family: Theme.fontMono
            font.pixelSize: Theme.textMd
            font.weight: Theme.weightSemiBold
            color: Theme.textPrimary
        }

        Badge {
            text: {
                if (root.riskLevel === "hardware_danger") return qsTr("HARDWARE DANGER")
                if (root.riskLevel === "hardware_state") return qsTr("HARDWARE STATE")
                if (root.riskLevel === "workspace_write") return qsTr("WORKSPACE WRITE")
                if (root.riskLevel === "shell") return qsTr("SHELL")
                return root.riskLevel.toUpperCase()
            }
            variant: root.isDanger ? "danger" : (root.riskLevel === "hardware_state" ? "warning" : (root.riskLevel === "shell" ? "purple" : "info"))
        }

        Item { Layout.fillWidth: true }

        Badge {
            text: qsTr("1 of %1").arg(root.queueTotal)
            variant: "neutral"
            compact: true
        }
    }

    // Summary Box
    Rectangle {
        Layout.fillWidth: true
        implicitHeight: sumText.implicitHeight + Theme.s3 * 2
        radius: Theme.radiusMd
        color: Theme.bgElevated
        border.color: Theme.borderSubtle
        border.width: 1

        Text {
            id: sumText
            anchors.fill: parent
            anchors.margins: Theme.s3
            text: root.req.summary || qsTr("Review requested operation parameters before continuing.")
            font.family: Theme.fontSans
            font.pixelSize: Theme.textBase
            color: Theme.textPrimary
            wrapMode: Text.WordWrap
        }
    }

    // Hard Safety Notice for hardware_danger
    Rectangle {
        visible: root.isDanger
        Layout.fillWidth: true
        implicitHeight: dangerNoticeRow.implicitHeight + Theme.s2 * 2
        radius: Theme.radiusMd
        color: Theme.dangerSubtle
        border.color: Theme.danger
        border.width: 1

        RowLayout {
            id: dangerNoticeRow
            anchors.fill: parent
            anchors.margins: Theme.s2 + 2
            spacing: Theme.s2

            Icon { name: Icons.triangleAlert; size: 15; color: Theme.danger }
            Text {
                Layout.fillWidth: true
                text: qsTr("High-risk hardware operation: 'Allow for session' is permanently disabled. Each invocation requires explicit confirmation.")
                font.family: Theme.fontSans
                font.pixelSize: Theme.textXs
                color: Theme.textPrimary
                wrapMode: Text.WordWrap
            }
        }
    }

    // Tabs: Changes / SVD vs Arguments
    TabBarLine {
        Layout.fillWidth: true
        currentId: root.activeTab
        tabs: [
            { id: "changes", label: root.diffPreview.length > 0 ? qsTr("Changes") : qsTr("Register / Preview"), icon: root.diffPreview.length > 0 ? Icons.fileDiff : Icons.cpu },
            { id: "args",    label: qsTr("Arguments"), icon: Icons.fileCode }
        ]
        onTabSelected: function(tid) { root.activeTab = tid }
    }

    // Changes / SVD Preview
    ColumnLayout {
        visible: root.activeTab === "changes"
        Layout.fillWidth: true
        spacing: Theme.s2

        DiffView {
            visible: root.diffPreview.length > 0
            Layout.fillWidth: true
            maxHeight: 240
            diffText: root.diffPreview
        }

        CodeBlock {
            visible: root.svdPreview.length > 0
            Layout.fillWidth: true
            maxHeight: 200
            language: qsTr("CMSIS-SVD Decode")
            text: root.svdPreview
        }

        Text {
            visible: root.diffPreview.length === 0 && root.svdPreview.length === 0
            Layout.fillWidth: true
            text: qsTr("No source diff or SVD register preview for this operation. Inspect the Arguments tab.")
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
    }

    // JSON Arguments View
    JsonView {
        visible: root.activeTab === "args"
        Layout.fillWidth: true
        maxHeight: 240
        jsonValue: root.detailsJson
    }

    // Optional Reviewer Note
    AppTextField {
        id: reasonField
        Layout.fillWidth: true
        placeholderText: qsTr("Optional decision reason or constraint for the Agent...")
        clearable: true
    }

    footer: [
        AppButton {
            variant: "danger"
            iconName: Icons.circleX
            text: qsTr("Deny")
            onClicked: root.submitDecision(false, "deny")
        },
        Item { Layout.fillWidth: true },
        AppButton {
            visible: !root.isDanger
            variant: "secondary"
            iconName: Icons.shieldCheck
            text: qsTr("Allow for session")
            onClicked: root.submitDecision(true, "session_tool")
        },
        AppButton {
            variant: "primary"
            iconName: Icons.check
            text: qsTr("Allow once")
            onClicked: root.submitDecision(true, "once")
        }
    ]
}
