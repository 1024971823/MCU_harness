pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    property int rowIndex: -1
    property string kind: "tool_call"
    property string toolCallId: ""
    property string toolName: ""
    property string toolTitle: ""
    property string toolIcon: ""
    property string riskLevel: "read_only"
    property string argsJson: ""
    property string resultJson: ""
    property string summary: ""
    property string status: "running"
    property real durationMs: 0
    property real startedMs: 0
    property bool expanded: false
    property bool isLastRunning: false

    signal reviewApprovalRequested()

    property real _nowMs: Date.now()
    property bool _copied: false

    readonly property color hue: PluginRegistry.riskHue(riskLevel)
    readonly property bool isApprovalWaiting: status === "running" && isLastRunning && Workbench.agentPhase === "approval"
    readonly property string compactArgs: compactArgsSummary(argsJson)
    readonly property bool hasDetails: argsJson.length > 0 || resultJson.length > 0 || summary.length > 0

    function compactArgsSummary(raw) {
        if (!raw || raw.length === 0) return ""
        try {
            var obj = JSON.parse(raw)
            if (!obj || typeof obj !== "object" || Array.isArray(obj)) return ""
            var keys = Object.keys(obj)
            var parts = []
            for (var i = 0; i < keys.length && parts.length < 2; ++i) {
                var k = keys[i]
                var v = obj[k]
                var vs = (typeof v === "string") ? v : JSON.stringify(v)
                if (vs === undefined) continue
                if (vs.length > 36) vs = vs.slice(0, 33) + "…"
                parts.push(k + "=" + vs)
            }
            return parts.join("  ")
        } catch (e) {
            return ""
        }
    }

    function riskVariant(risk) {
        switch (String(risk || "").toLowerCase()) {
        case "workspace_write": return "purple"
        case "shell":           return "info"
        case "hardware_state":  return "warning"
        case "hardware_danger": return "danger"
        default:                return "neutral"
        }
    }

    function liveElapsedText() {
        var ms = Math.max(0, startedMs > 0 ? (_nowMs - startedMs) : 0)
        return (ms / 1000.0).toFixed(1) + "s"
    }

    Component.onCompleted: _nowMs = Date.now()
    onStartedMsChanged: _nowMs = Date.now()

    Timer {
        interval: 100
        repeat: true
        running: root.status === "running" && root.visible && !AppInfo.screenshotMode
        onTriggered: root._nowMs = Date.now()
    }

    Timer {
        id: copyTimer
        interval: 1500
        onTriggered: root._copied = false
    }

    implicitHeight: card.implicitHeight

    Rectangle {
        id: card
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 40
        implicitHeight: cardCol.implicitHeight + Theme.s2 * 2 + 2
        radius: Theme.radiusLg
        color: root.expanded ? Theme.bgSurface : (cardHover.hovered ? Theme.bgHover : Theme.bgElevated)
        border.color: root.isApprovalWaiting
            ? Theme.alpha(Theme.hueAmber, 0.55)
            : (root.status === "denied" || root.status === "error"
                ? Theme.alpha(Theme.hueRose, 0.45)
                : (cardHover.hovered || root.expanded ? Theme.borderStrong : Theme.borderSubtle))
        border.width: 1

        Behavior on color {
            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
        }

        ColumnLayout {
            id: cardCol
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: Theme.s2 + 1
            spacing: Theme.s2

            // Header row (clickable)
            Item {
                Layout.fillWidth: true
                implicitHeight: Math.max(28, headerRow.implicitHeight)

                HoverHandler {
                    id: cardHover
                    enabled: root.hasDetails
                    cursorShape: root.hasDetails ? Qt.PointingHandCursor : Qt.ArrowCursor
                }

                TapHandler {
                    enabled: root.hasDetails
                    onTapped: {
                        if (root.rowIndex >= 0) {
                            Workbench.messagesModel.setExpanded(root.rowIndex, !root.expanded)
                        }
                    }
                }

                RowLayout {
                    id: headerRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Theme.s2

                    IconChip {
                        size: 28
                        icon: root.toolIcon.length > 0 ? root.toolIcon : Icons.wrench
                        hue: root.hue
                    }

                    Text {
                        text: root.toolTitle.length > 0 ? root.toolTitle : root.toolName
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textBase
                        font.weight: Theme.weightSemiBold
                        color: Theme.textPrimary
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: root.compactArgs.length > 0
                        text: root.compactArgs
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.textSm
                        color: Theme.textMuted
                        elide: Text.ElideRight
                    }

                    Item {
                        Layout.fillWidth: true
                        visible: root.compactArgs.length === 0
                    }

                    Badge {
                        visible: root.riskLevel.length > 0 && root.riskLevel !== "read_only"
                        text: root.riskLevel
                        variant: root.riskVariant(root.riskLevel)
                        compact: true
                    }

                    // Running status
                    RowLayout {
                        visible: root.status === "running"
                        spacing: Theme.s1 + 2

                        Spinner {
                            size: 13
                            color: root.hue
                        }

                        Text {
                            text: root.liveElapsedText()
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.textXs
                            color: Theme.textSecondary
                        }
                    }

                    // OK status
                    RowLayout {
                        visible: root.status === "ok"
                        spacing: Theme.s1

                        Icon {
                            name: Icons.circleCheck
                            size: 14
                            color: Theme.hueEmerald
                        }

                        Text {
                            text: Utils.formatDurationMs(root.durationMs)
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.textXs
                            color: Theme.textMuted
                        }
                    }

                    // Error status
                    RowLayout {
                        visible: root.status === "error"
                        spacing: Theme.s1

                        Icon {
                            name: Icons.circleX
                            size: 14
                            color: Theme.hueRose
                        }

                        Text {
                            text: qsTr("Error")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textXs
                            font.weight: Theme.weightMedium
                            color: Theme.hueRose
                        }
                    }

                    // Denied status
                    RowLayout {
                        visible: root.status === "denied"
                        spacing: Theme.s1

                        Icon {
                            name: Icons.shieldOff
                            size: 14
                            color: Theme.hueRose
                        }

                        Text {
                            text: qsTr("Blocked")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textXs
                            font.weight: Theme.weightMedium
                            color: Theme.hueRose
                        }
                    }

                    Icon {
                        visible: root.hasDetails
                        name: root.expanded ? Icons.chevronDown : Icons.chevronRight
                        size: 13
                        color: Theme.textMuted
                    }
                }
            }

            // Inline awaiting approval banner
            Rectangle {
                visible: root.isApprovalWaiting
                Layout.fillWidth: true
                implicitHeight: approvalRow.implicitHeight + Theme.s2
                radius: Theme.radiusMd
                color: Theme.alpha(Theme.hueAmber, 0.12)
                border.color: Theme.alpha(Theme.hueAmber, 0.40)
                border.width: 1

                HoverHandler {
                    id: approvalHover
                    cursorShape: Qt.PointingHandCursor
                }
                TapHandler {
                    onTapped: root.reviewApprovalRequested()
                }

                RowLayout {
                    id: approvalRow
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s2 + 2
                    anchors.rightMargin: Theme.s2 + 2
                    spacing: Theme.s2

                    Icon {
                        name: Icons.shieldAlert
                        size: 14
                        color: Theme.hueAmber
                    }

                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Awaiting approval · Review")
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textSm
                        font.weight: Theme.weightSemiBold
                        color: Theme.hueAmber
                    }

                    Icon {
                        name: Icons.chevronRight
                        size: 13
                        color: Theme.hueAmber
                    }
                }
            }

            // Expandable details drawer
            ColumnLayout {
                visible: root.expanded && root.hasDetails
                Layout.fillWidth: true
                spacing: Theme.s2

                Divider { Layout.fillWidth: true }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    Text {
                        Layout.fillWidth: true
                        visible: root.summary.length > 0
                        text: root.summary
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textSm
                        color: Theme.textSecondary
                        wrapMode: Text.WordWrap
                    }

                    Item {
                        Layout.fillWidth: true
                        visible: root.summary.length === 0
                    }

                    IconButton {
                        buttonSize: 24
                        iconSize: 13
                        iconName: root._copied ? Icons.check : Icons.copy
                        iconColor: root._copied ? Theme.success : Theme.textSecondary
                        tooltipText: root._copied ? qsTr("Copied") : qsTr("Copy tool details")
                        onClicked: {
                            var parts = []
                            if (root.argsJson.length > 0) parts.push("Arguments:\n" + root.argsJson)
                            if (root.resultJson.length > 0) parts.push("Result:\n" + root.resultJson)
                            Utils.copyToClipboard(parts.join("\n\n"))
                            root._copied = true
                            copyTimer.restart()
                        }
                    }
                }

                Text {
                    visible: root.argsJson.length > 0
                    text: qsTr("Arguments")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textXs
                    font.weight: Theme.weightSemiBold
                    color: Theme.textMuted
                }

                JsonView {
                    visible: root.argsJson.length > 0
                    Layout.fillWidth: true
                    maxHeight: 160
                    jsonValue: root.argsJson
                }

                Text {
                    visible: root.resultJson.length > 0
                    text: qsTr("Result")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textXs
                    font.weight: Theme.weightSemiBold
                    color: Theme.textMuted
                }

                JsonView {
                    visible: root.resultJson.length > 0
                    Layout.fillWidth: true
                    maxHeight: 220
                    jsonValue: root.resultJson
                }
            }
        }
    }
}
