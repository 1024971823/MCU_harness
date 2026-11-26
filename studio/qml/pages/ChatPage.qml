pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"
import "../chat"

Item {
    id: root

    signal openSettingsRequested(string section)
    signal openApprovalRequested()
    signal openPluginRequested(string pluginId)

    property bool stickToBottom: true

    function focusComposer() {
        composer.focusInput()
    }

    function insertSkill(cmd) {
        composer.insertSkill(cmd)
    }

    function sendCurrentPrompt() {
        composer.sendCurrent()
    }

    // Track last message kind and last assistant answer / running tool rows without brittle role math
    readonly property int messageCount: Workbench.messagesModel ? Workbench.messagesModel.count : 0

    property int _lastAssistantRow: -1
    property int _lastRunningToolRow: -1
    property int _lastTurnStartRow: -1
    property string _lastEntryKind: ""

    function refreshModelMarkers() {
        var m = Workbench.messagesModel
        if (!m || m.count === 0) {
            _lastAssistantRow = -1
            _lastRunningToolRow = -1
            _lastTurnStartRow = -1
            _lastEntryKind = ""
            return
        }
        var lastAsst = -1
        var lastRun = -1
        var lastTurnStart = -1
        var lastKind = ""
        for (var i = 0; i < timelineList.count; ++i) {
            var it = timelineList.itemAtIndex(i)
            if (it) {
                var k = it["kind"]
                if (k !== undefined) {
                    if (i === timelineList.count - 1) lastKind = k
                    if (k === "assistant") lastAsst = i
                    if (k === "tool_call" && it["status"] === "running") lastRun = i
                    if (it["isAssistantBlock"] && it["groupStart"]) lastTurnStart = i
                }
            }
        }
        _lastAssistantRow = lastAsst
        _lastRunningToolRow = lastRun
        _lastTurnStartRow = lastTurnStart
        if (lastKind.length > 0) _lastEntryKind = lastKind
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Top Alerts / Banners
        ColumnLayout {
            id: alertsCol
            visible: engineErrorBanner.visible || pendingApprovalBanner.visible
            Layout.fillWidth: true
            Layout.fillHeight: false
            Layout.preferredHeight: visible ? implicitHeight : 0
            spacing: 0

            Rectangle {
                id: engineErrorBanner
                visible: Workbench.engineStatus === "error"
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? 38 : 0
                color: Theme.dangerSubtle

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s4
                    anchors.rightMargin: Theme.s4
                    spacing: Theme.s2

                    Icon { name: Icons.circleAlert; size: 15; color: Theme.danger }

                    Text {
                        Layout.fillWidth: true
                        text: Workbench.engineError
                            ? qsTr("Backend engine offline: %1").arg(Workbench.engineError)
                            : qsTr("Backend engine offline.")
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textSm
                        color: Theme.textPrimary
                        elide: Text.ElideRight
                    }

                    AppButton {
                        buttonSize: "sm"
                        variant: "secondary"
                        iconName: Icons.rotateCcw
                        text: qsTr("Restart Engine")
                        onClicked: Workbench.restartEngine()
                    }
                }
            }

            Rectangle {
                id: pendingApprovalBanner
                visible: (Workbench.pendingApprovalsCount || 0) > 0
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? 40 : 0
                color: Theme.warningSubtle

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s4
                    anchors.rightMargin: Theme.s4
                    spacing: Theme.s2

                    Icon { name: Icons.shieldAlert; size: 15; color: Theme.warning }

                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Hardware Safety Interlock paused execution (%1 pending approval).").arg(Workbench.pendingApprovalsCount || 0)
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textSm
                        font.weight: Theme.weightMedium
                        color: Theme.textPrimary
                        elide: Text.ElideRight
                    }

                    AppButton {
                        buttonSize: "sm"
                        variant: "danger"
                        iconName: Icons.shieldAlert
                        text: qsTr("Review Approval")
                        onClicked: root.openApprovalRequested()
                    }
                }
            }
        }

        // Main Chat Content Area
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ChatEmptyState {
                anchors.fill: parent
                visible: root.messageCount === 0
                onSkillClicked: function(cmd) {
                    root.insertSkill(cmd)
                }
            }

            ListView {
                id: timelineList
                visible: root.messageCount > 0
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(parent.width - Theme.s6 * 2, Theme.chatMaxWidth)
                topMargin: Theme.s4
                bottomMargin: Theme.s5
                spacing: 0
                clip: true
                model: Workbench.messagesModel
                ScrollBar.vertical: AppScrollBar {}

                add: Transition {
                    NumberAnimation {
                        property: "opacity"
                        from: 0.0
                        to: 1.0
                        duration: Theme.animationsEnabled ? 200 : 0
                        easing.type: Theme.easingType
                    }
                    NumberAnimation {
                        property: "y"
                        from: ViewTransition.destination.y + 8
                        to: ViewTransition.destination.y
                        duration: Theme.animationsEnabled ? 200 : 0
                        easing.type: Theme.easingType
                    }
                }

                onAtYEndChanged: {
                    if (atYEnd) root.stickToBottom = true
                }
                onMovementStarted: {
                    if (!atYEnd) root.stickToBottom = false
                }
                onCountChanged: {
                    if (count === 0) {
                        root._lastAssistantRow = -1
                        root._lastRunningToolRow = -1
                        root._lastTurnStartRow = -1
                        root._lastEntryKind = ""
                    } else {
                        Qt.callLater(root.refreshModelMarkers)
                    }
                    if (root.stickToBottom && count > 0) {
                        Qt.callLater(function() {
                            if (timelineList.count > 0) timelineList.positionViewAtEnd()
                        })
                    }
                }
                onContentHeightChanged: {
                    if (root.stickToBottom && count > 0) {
                        Qt.callLater(function() {
                            if (timelineList.count > 0) timelineList.positionViewAtEnd()
                        })
                    }
                }

                footer: Item {
                    id: pendingFooter
                    width: timelineList.width
                    readonly property bool showPending: Workbench.agentPhase === "waiting" && root._lastEntryKind === "user"
                    height: showPending ? pendingCol.implicitHeight + Theme.s4 : 0
                    visible: showPending

                    property real _shimmerPhase: 0.45

                    SequentialAnimation on _shimmerPhase {
                        running: pendingFooter.showPending && pendingFooter.parent !== null
                        loops: Animation.Infinite
                        NumberAnimation { from: 0.45; to: 1.0; duration: 900; easing.type: Easing.InOutSine }
                        NumberAnimation { from: 1.0; to: 0.45; duration: 900; easing.type: Easing.InOutSine }
                    }

                    ColumnLayout {
                        id: pendingCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.topMargin: Theme.s3
                        spacing: Theme.s2

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s3

                            AgentOrb {
                                size: 28
                                spinning: true
                                icon: Icons.sparkles
                            }

                            Text {
                                text: qsTr("Harness Agent")
                                font.family: Theme.fontSans
                                font.pixelSize: Theme.textBase
                                font.weight: Theme.weightSemiBold
                                color: Theme.textPrimary
                            }

                            Pill {
                                compact: true
                                subtle: false
                                text: Workbench.model || "mock-model"
                            }

                            Item { Layout.fillWidth: true }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Layout.leftMargin: 40
                            spacing: Theme.s2
                            opacity: pendingFooter._shimmerPhase

                            Text {
                                text: qsTr("Connecting to model…")
                                font.family: Theme.fontSans
                                font.pixelSize: Theme.textBase
                                color: Theme.textMuted
                            }

                            TypingDots {
                                dotSize: 4
                                color: Theme.brandC
                                active: pendingFooter.showPending
                            }
                        }
                    }
                }

                delegate: Item {
                    id: msgDelegate
                    required property int index
                    required property string kind
                    required property string text
                    required property bool streaming
                    required property string toolCallId
                    required property string toolName
                    required property string toolTitle
                    required property string toolIcon
                    required property string riskLevel
                    required property string argsJson
                    required property string resultJson
                    required property string summary
                    required property string status
                    required property string level
                    required property string timestamp
                    required property real durationMs
                    required property real startedMs
                    required property bool expanded
                    required property bool groupStart
                    required property bool groupEnd

                    readonly property bool isAssistantBlock: kind !== "user" && kind !== "notice"
                    readonly property int topPad: {
                        if (kind === "user" || kind === "notice") return index === 0 ? Theme.s2 : Theme.s4
                        return groupStart ? (index === 0 ? Theme.s2 : Theme.s4) : Theme.s1 + 2
                    }
                    readonly property int bottomPad: {
                        if (kind === "user" || kind === "notice") return Theme.s2
                        return groupEnd ? Theme.s3 : Theme.s1 + 2
                    }

                    width: timelineList.width
                    height: blockCol.implicitHeight + topPad + bottomPad

                    Component.onCompleted: Qt.callLater(root.refreshModelMarkers)
                    onKindChanged: Qt.callLater(root.refreshModelMarkers)
                    onStatusChanged: Qt.callLater(root.refreshModelMarkers)

                    // Continuous 1px vertical timeline guide under AgentOrb across assistant turn blocks
                    Rectangle {
                        id: timelineGuide
                        visible: msgDelegate.isAssistantBlock && !(msgDelegate.groupStart && msgDelegate.groupEnd && msgDelegate.kind === "assistant" && msgDelegate.text.length === 0)
                        x: 14
                        width: 1
                        color: Theme.borderSubtle
                        y: msgDelegate.groupStart ? (msgDelegate.topPad + 28 + Theme.s1) : 0
                        height: {
                            var startY = y
                            var endY = msgDelegate.groupEnd ? (msgDelegate.topPad + blockCol.implicitHeight) : msgDelegate.height
                            return Math.max(0, endY - startY)
                        }
                    }

                    ColumnLayout {
                        id: blockCol
                        x: 0
                        y: msgDelegate.topPad
                        width: parent.width
                        spacing: Theme.s2

                        // Assistant Turn Header (only on groupStart)
                        RowLayout {
                            id: turnHeader
                            visible: msgDelegate.isAssistantBlock && msgDelegate.groupStart
                            Layout.fillWidth: true
                            spacing: Theme.s3

                            AgentOrb {
                                size: 28
                                spinning: Workbench.turnRunning && msgDelegate.index >= root._lastTurnStartRow
                                icon: Icons.sparkles
                            }

                            Text {
                                text: qsTr("Harness Agent")
                                font.family: Theme.fontSans
                                font.pixelSize: Theme.textBase
                                font.weight: Theme.weightSemiBold
                                color: Theme.textPrimary
                            }

                            Pill {
                                compact: true
                                subtle: false
                                text: Workbench.model || "mock-model"
                            }

                            Item { Layout.fillWidth: true }
                        }

                        UserBubble {
                            visible: msgDelegate.kind === "user"
                            Layout.fillWidth: true
                            text: msgDelegate.text
                            timestamp: msgDelegate.timestamp
                        }

                        ThinkingBlock {
                            visible: msgDelegate.kind === "thinking"
                            Layout.fillWidth: true
                            rowIndex: msgDelegate.index
                            text: msgDelegate.text
                            streaming: msgDelegate.streaming
                            expanded: msgDelegate.expanded
                            durationMs: msgDelegate.durationMs
                            startedMs: msgDelegate.startedMs
                        }

                        ToolStep {
                            visible: msgDelegate.kind === "tool_call" || msgDelegate.kind === "tool_result"
                            Layout.fillWidth: true
                            rowIndex: msgDelegate.index
                            kind: msgDelegate.kind
                            toolCallId: msgDelegate.toolCallId
                            toolName: msgDelegate.toolName
                            toolTitle: msgDelegate.toolTitle
                            toolIcon: msgDelegate.toolIcon
                            riskLevel: msgDelegate.riskLevel
                            argsJson: msgDelegate.argsJson
                            resultJson: msgDelegate.resultJson
                            summary: msgDelegate.summary
                            status: msgDelegate.status
                            durationMs: msgDelegate.durationMs
                            startedMs: msgDelegate.startedMs
                            expanded: msgDelegate.expanded
                            isLastRunning: msgDelegate.index === root._lastRunningToolRow
                            onReviewApprovalRequested: root.openApprovalRequested()
                        }

                        AnswerBlock {
                            visible: msgDelegate.kind === "assistant"
                            Layout.fillWidth: true
                            text: msgDelegate.text
                            streaming: msgDelegate.streaming
                            timestamp: msgDelegate.timestamp
                            isLastAssistantAnswer: msgDelegate.index === root._lastAssistantRow
                        }

                        NoticeRow {
                            visible: msgDelegate.kind === "notice"
                            Layout.fillWidth: true
                            level: msgDelegate.level
                            text: msgDelegate.text
                            timestamp: msgDelegate.timestamp
                        }
                    }
                }
            }

            // Floating round Jump-to-latest button when scrolled up
            Rectangle {
                id: jumpBtn
                visible: timelineList.count > 0 && !timelineList.atYEnd && !root.stickToBottom
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: Theme.s3
                width: 36
                height: 36
                radius: 18
                color: jumpHover.hovered ? Theme.bgHover : Theme.bgElevated
                border.color: jumpHover.hovered ? Theme.accent : Theme.borderStrong
                border.width: 1

                Behavior on color {
                    ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                }
                Behavior on border.color {
                    ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                }

                HoverHandler {
                    id: jumpHover
                    cursorShape: Qt.PointingHandCursor
                }
                TapHandler {
                    onTapped: {
                        root.stickToBottom = true
                        timelineList.positionViewAtEnd()
                    }
                }

                Icon {
                    anchors.centerIn: parent
                    name: Icons.arrowDown
                    size: 16
                    color: jumpHover.hovered ? Theme.accent : Theme.textPrimary
                }

                AppToolTip {
                    visible: jumpHover.hovered
                    text: qsTr("Jump to latest")
                }
            }
        }

        // Bottom Dock: ActivityPill + Composer (centered identically to timelineList)
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: false
            implicitHeight: bottomDock.implicitHeight + Theme.s2 + Theme.s4

            ColumnLayout {
                id: bottomDock
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.top: parent.top
                anchors.topMargin: Theme.s2
                width: Math.min(parent.width - Theme.s6 * 2, Theme.chatMaxWidth)
                spacing: Theme.s2

                ActivityPill {
                    id: activityPill
                    Layout.alignment: Qt.AlignHCenter
                    Layout.maximumWidth: bottomDock.width
                    onReviewApprovalRequested: root.openApprovalRequested()
                }

                Composer {
                    id: composer
                    Layout.fillWidth: true
                    onSendRequested: function(promptText) {
                        root.stickToBottom = true
                        Workbench.sendMessage(promptText)
                    }
                    onOpenSettingsRequested: function(section) {
                        root.openSettingsRequested(section)
                    }
                }
            }
        }
    }
}
