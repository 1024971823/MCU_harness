pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root

    signal openSettingsRequested(string section)
    signal openApprovalRequested()
    signal openLicenseRequested()

    property bool stickToBottom: true

    function focusComposer() {
        composerInput.forceActiveFocus()
    }

    function insertPlaybook(text) {
        composerInput.text = text
        composerInput.cursorPosition = composerInput.text.length
        composerInput.forceActiveFocus()
    }

    function sendCurrentPrompt() {
        var trimmed = composerInput.text.trim()
        if (trimmed.length === 0 || Workbench.turnRunning) return
        slashPopup.close()
        stickToBottom = true
        Workbench.sendMessage(trimmed)
        composerInput.text = ""
    }

    function cycleInterlockMode() {
        var modes = ["interactive", "auto_safe", "read_only"]
        var idx = modes.indexOf(Workbench.interlockMode || "interactive")
        var next = modes[(idx + 1) % modes.length]
        Workbench.setInterlockMode(next)
    }

    readonly property var suggestions: [
        { icon: Icons.bug,        cmd: "/crash",   title: qsTr("Diagnose HardFault"),   desc: qsTr("Decode CFSR/HFSR, stacked PC/LR & RTOS tasks") },
        { icon: Icons.wrench,     cmd: "/doctor",  title: qsTr("Peripheral Doctor"),    desc: qsTr("Verify RCC clocks, NVIC, GPIO AF & live SVD diff") },
        { icon: Icons.plugZap,    cmd: "/bringup", title: qsTr("Board Bring-Up"),       desc: qsTr("Inspect .ioc wiring, build ELF & check boot logs") },
        { icon: Icons.layers,     cmd: "rtos",     title: qsTr("Inspect RTOS Stacks"),  desc: qsTr("Analyze task priorities, states & stack high-water marks") },
        { icon: Icons.microchip,  cmd: "arch",     title: qsTr("Check Architecture"),   desc: qsTr("Audit HAL/BSP boundaries & ISR safety compliance") },
        { icon: Icons.flask,      cmd: "/hil",     title: qsTr("Run HIL Verification"), desc: qsTr("Execute hardware-in-the-loop assertions on target") }
    ]

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Top Alerts / Banners
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0

            // Engine Error Banner
            Rectangle {
                visible: Workbench.engineStatus === "error"
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? 40 : 0
                color: Theme.dangerSubtle

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s4
                    anchors.rightMargin: Theme.s4
                    spacing: Theme.s2

                    Icon { name: Icons.circleAlert; size: 15; color: Theme.danger }
                    Text {
                        Layout.fillWidth: true
                        text: Workbench.engineError ? qsTr("Backend engine offline: %1").arg(Workbench.engineError) : qsTr("Backend engine offline.")
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

            // License Expired Banner
            Rectangle {
                visible: License.status === "expired"
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? 40 : 0
                color: Theme.warningSubtle

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s4
                    anchors.rightMargin: Theme.s4
                    spacing: Theme.s2

                    Icon { name: Icons.keyRound; size: 15; color: Theme.warning }
                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Your trial has expired. Activate a license key to unlock unlimited hardware runs.")
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textSm
                        color: Theme.textPrimary
                        elide: Text.ElideRight
                    }
                    AppButton {
                        buttonSize: "sm"
                        variant: "primary"
                        text: qsTr("Activate License")
                        onClicked: root.openLicenseRequested()
                    }
                }
            }

            // Pending Approval Banner
            Rectangle {
                visible: (Workbench.pendingApprovalsCount || 0) > 0
                Layout.fillWidth: true
                Layout.preferredHeight: visible ? 42 : 0
                color: Theme.dangerSubtle

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s4
                    anchors.rightMargin: Theme.s4
                    spacing: Theme.s2

                    Icon { name: Icons.shieldAlert; size: 16; color: Theme.danger }
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

        // Main centered column
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            // Empty State (no messages yet)
            Flickable {
                id: emptyFlick
                visible: timelineList.count === 0
                anchors.fill: parent
                clip: true
                contentWidth: width
                contentHeight: emptyCol.implicitHeight + Theme.s8 * 2
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: AppScrollBar {}

                ColumnLayout {
                    id: emptyCol
                    x: Math.max(Theme.s6, (emptyFlick.width - width) / 2)
                    y: Math.max(Theme.s6, (emptyFlick.height - height) / 2 - Theme.s2)
                    width: Math.min(emptyFlick.width - Theme.s6 * 2, 820)
                    spacing: Theme.s5

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignHCenter
                        spacing: Theme.s2

                        Rectangle {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.preferredWidth: 52
                            Layout.preferredHeight: 52
                            radius: Theme.radiusXl
                            color: Theme.accentSubtle
                            border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.35)
                            border.width: 1

                            Icon {
                                anchors.centerIn: parent
                                name: Icons.sparkles
                                size: 24
                                color: Theme.accent
                            }
                        }

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            text: qsTr("MCU Harness Agent")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.text2xl
                            font.weight: Theme.weightSemiBold
                            color: Theme.textPrimary
                        }

                        Text {
                            Layout.alignment: Qt.AlignHCenter
                            Layout.fillWidth: true
                            Layout.maximumWidth: 640
                            text: qsTr("Closed-loop Cortex-M debugging with CMSIS-SVD register decode, non-blocking RTT/UART logs, and hardware safety interlocks.")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textBase
                            color: Theme.textSecondary
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                        }
                    }

                    // 3x2 Suggestion Grid
                    GridLayout {
                        Layout.fillWidth: true
                        columns: emptyCol.width < 680 ? 2 : 3
                        columnSpacing: Theme.s3
                        rowSpacing: Theme.s3

                        Repeater {
                            model: root.suggestions

                            delegate: Rectangle {
                                id: sugCard
                                required property var modelData

                                Layout.fillWidth: true
                                implicitHeight: sugCol.implicitHeight + Theme.s4 * 2
                                radius: Theme.radiusLg
                                color: sugHover.hovered ? Theme.bgElevated : Theme.bgSurface
                                border.color: sugHover.hovered ? Theme.borderStrong : Theme.borderSubtle
                                border.width: 1

                                Behavior on color { ColorAnimation { duration: Theme.animFast } }
                                Behavior on border.color { ColorAnimation { duration: Theme.animFast } }

                                HoverHandler { id: sugHover }
                                TapHandler {
                                    onTapped: {
                                        if (sugCard.modelData.cmd.indexOf("/") === 0) {
                                            root.insertPlaybook(sugCard.modelData.cmd + " ")
                                        } else {
                                            root.insertPlaybook(sugCard.modelData.title + ": " + sugCard.modelData.desc)
                                        }
                                    }
                                }

                                ColumnLayout {
                                    id: sugCol
                                    anchors.fill: parent
                                    anchors.margins: Theme.s4
                                    spacing: Theme.s2

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Theme.s2

                                        Icon {
                                            name: sugCard.modelData.icon
                                            size: 15
                                            color: Theme.accent
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            text: sugCard.modelData.title
                                            font.family: Theme.fontSans
                                            font.pixelSize: Theme.textBase
                                            font.weight: Theme.weightSemiBold
                                            color: Theme.textPrimary
                                            elide: Text.ElideRight
                                        }

                                        Badge {
                                            visible: sugCard.modelData.cmd.indexOf("/") === 0
                                            text: sugCard.modelData.cmd
                                            variant: "accent"
                                            compact: true
                                        }
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: sugCard.modelData.desc
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textSm
                                        color: Theme.textSecondary
                                        wrapMode: Text.WordWrap
                                        maximumLineCount: 2
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Active Conversation ListView
            ListView {
                id: timelineList
                visible: count > 0
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(parent.width - Theme.s4 * 2, 860)
                topMargin: Theme.s4
                bottomMargin: Theme.s6
                spacing: Theme.s3
                clip: true
                model: Workbench.messagesModel
                ScrollBar.vertical: AppScrollBar {}

                onAtYEndChanged: {
                    if (atYEnd) root.stickToBottom = true
                }
                onMovementStarted: {
                    if (!atYEnd) root.stickToBottom = false
                }
                onCountChanged: {
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

                delegate: Item {
                    id: msgWrap
                    required property string role
                    required property string text
                    required property string toolName
                    required property string argsPretty
                    required property bool ok
                    required property bool denied
                    required property bool hardDenied
                    required property string summary
                    required property string rawJson
                    required property string level
                    required property bool streaming
                    property bool expanded: false

                    width: timelineList.width
                    height: msgCol.implicitHeight

                    ColumnLayout {
                        id: msgCol
                        width: parent.width
                        spacing: Theme.s2

                        // USER MESSAGE
                        RowLayout {
                            visible: msgWrap.role === "user"
                            Layout.fillWidth: true
                            spacing: Theme.s2

                            Item { Layout.fillWidth: true }

                            Rectangle {
                                Layout.maximumWidth: msgWrap.width * 0.82
                                implicitWidth: Math.min(msgWrap.width * 0.82, userMsgTxt.implicitWidth + Theme.s4 * 2)
                                implicitHeight: userMsgTxt.implicitHeight + Theme.s3 * 2
                                radius: Theme.radiusLg
                                color: Theme.accentSubtle
                                border.color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.35)
                                border.width: 1

                                TextEdit {
                                    id: userMsgTxt
                                    anchors.fill: parent
                                    anchors.margins: Theme.s3
                                    leftPadding: Theme.s1
                                    rightPadding: Theme.s1
                                    readOnly: true
                                    selectByMouse: true
                                    wrapMode: TextEdit.Wrap
                                    text: msgWrap.text
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textBase
                                    color: Theme.textPrimary
                                }
                            }
                        }

                        // ASSISTANT MESSAGE
                        Rectangle {
                            visible: msgWrap.role === "assistant"
                            Layout.fillWidth: true
                            implicitHeight: asstCol.implicitHeight + Theme.s4 * 2
                            radius: Theme.radiusLg
                            color: Theme.bgSurface
                            border.color: Theme.borderSubtle
                            border.width: 1

                            ColumnLayout {
                                id: asstCol
                                anchors.fill: parent
                                anchors.margins: Theme.s4
                                spacing: Theme.s2

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    Rectangle {
                                        Layout.preferredWidth: 22
                                        Layout.preferredHeight: 22
                                        radius: 6
                                        color: Theme.accentSubtle

                                        Icon {
                                            anchors.centerIn: parent
                                            name: Icons.agent
                                            size: 13
                                            color: Theme.accent
                                        }
                                    }

                                    Text {
                                        text: qsTr("Harness Agent")
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textSm
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.textSecondary
                                    }

                                    Spinner {
                                        visible: msgWrap.streaming
                                        size: 12
                                    }

                                    Item { Layout.fillWidth: true }

                                    IconButton {
                                        visible: msgWrap.text.length > 0
                                        buttonSize: 24
                                        iconSize: 13
                                        iconName: Icons.copy
                                        tooltipText: qsTr("Copy response")
                                        onClicked: Utils.copyToClipboard(msgWrap.text)
                                    }
                                }

                                MarkdownText {
                                    Layout.fillWidth: true
                                    text: msgWrap.text
                                }
                            }
                        }

                        // TOOL CALL CARD
                        Rectangle {
                            visible: msgWrap.role === "tool_call"
                            Layout.fillWidth: true
                            implicitHeight: tcCol.implicitHeight + Theme.s3 * 2
                            radius: Theme.radiusMd
                            color: Theme.bgElevated
                            border.color: Theme.borderSubtle
                            border.width: 1

                            ColumnLayout {
                                id: tcCol
                                anchors.fill: parent
                                anchors.margins: Theme.s3
                                spacing: Theme.s2

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    Icon { name: Icons.wrench; size: 14; color: Theme.info }
                                    Text {
                                        text: msgWrap.toolName
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textSm
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.textPrimary
                                    }
                                    Badge { text: qsTr("CALL"); variant: "info"; compact: true }
                                    Item { Layout.fillWidth: true }
                                    IconButton {
                                        buttonSize: 22
                                        iconSize: 13
                                        iconName: msgWrap.expanded ? Icons.chevronUp : Icons.chevronDown
                                        onClicked: msgWrap.expanded = !msgWrap.expanded
                                    }
                                }

                                JsonView {
                                    visible: msgWrap.expanded && msgWrap.argsPretty.length > 0
                                    Layout.fillWidth: true
                                    maxHeight: 180
                                    jsonValue: msgWrap.argsPretty
                                }
                            }
                        }

                        // TOOL RESULT CARD
                        Rectangle {
                            visible: msgWrap.role === "tool_result"
                            Layout.fillWidth: true
                            implicitHeight: trCol.implicitHeight + Theme.s3 * 2
                            radius: Theme.radiusMd
                            color: Theme.bgSurface
                            border.color: msgWrap.hardDenied || !msgWrap.ok ? Theme.danger : (msgWrap.denied ? Theme.warning : Theme.borderSubtle)
                            border.width: 1

                            ColumnLayout {
                                id: trCol
                                anchors.fill: parent
                                anchors.margins: Theme.s3
                                spacing: Theme.s2

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    Icon {
                                        name: msgWrap.hardDenied ? Icons.shieldOff : (msgWrap.denied ? Icons.shieldAlert : (msgWrap.ok ? Icons.circleCheck : Icons.circleX))
                                        size: 14
                                        color: msgWrap.hardDenied || !msgWrap.ok ? Theme.danger : (msgWrap.denied ? Theme.warning : Theme.success)
                                    }

                                    Text {
                                        text: msgWrap.toolName
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textSm
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.textPrimary
                                    }

                                    Badge {
                                        text: msgWrap.hardDenied ? qsTr("HARD DENIED") : (msgWrap.denied ? qsTr("DENIED") : (msgWrap.ok ? qsTr("OK") : qsTr("ERROR")))
                                        variant: msgWrap.hardDenied || !msgWrap.ok ? "danger" : (msgWrap.denied ? "warning" : "success")
                                        compact: true
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: msgWrap.summary
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textSm
                                        color: Theme.textSecondary
                                        elide: Text.ElideRight
                                    }

                                    IconButton {
                                        visible: msgWrap.rawJson.length > 0
                                        buttonSize: 22
                                        iconSize: 13
                                        iconName: msgWrap.expanded ? Icons.chevronUp : Icons.chevronDown
                                        onClicked: msgWrap.expanded = !msgWrap.expanded
                                    }
                                }

                                JsonView {
                                    visible: msgWrap.expanded && msgWrap.rawJson.length > 0
                                    Layout.fillWidth: true
                                    maxHeight: 220
                                    jsonValue: msgWrap.rawJson
                                }
                            }
                        }

                        // APPROVAL / NOTICE CARD
                        Rectangle {
                            visible: msgWrap.role === "notice" || msgWrap.role === "approval"
                            Layout.fillWidth: true
                            implicitHeight: noticeRow.implicitHeight + Theme.s2 * 2
                            radius: Theme.radiusMd
                            color: msgWrap.level === "error" ? Theme.dangerSubtle : (msgWrap.level === "warn" ? Theme.warningSubtle : Theme.bgElevated)
                            border.color: msgWrap.level === "error" ? Theme.danger : (msgWrap.level === "warn" ? Theme.warning : Theme.borderSubtle)
                            border.width: 1

                            RowLayout {
                                id: noticeRow
                                anchors.fill: parent
                                anchors.margins: Theme.s2 + 2
                                spacing: Theme.s2

                                Icon {
                                    name: msgWrap.role === "approval" ? Icons.shieldCheck : (msgWrap.level === "error" ? Icons.circleAlert : (msgWrap.level === "warn" ? Icons.triangleAlert : Icons.info))
                                    size: 14
                                    color: msgWrap.level === "error" ? Theme.danger : (msgWrap.level === "warn" ? Theme.warning : Theme.info)
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: msgWrap.text
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textSm
                                    color: Theme.textPrimary
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                    }
                }
            }

            // Floating Jump to Latest button
            AppButton {
                visible: timelineList.count > 0 && !timelineList.atYEnd && !root.stickToBottom
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: Theme.s3
                buttonSize: "sm"
                variant: "secondary"
                iconName: Icons.chevronDown
                text: qsTr("Jump to latest")
                onClicked: {
                    root.stickToBottom = true
                    timelineList.positionViewAtEnd()
                }
            }
        }

        // Composer Container (max 860px centered)
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: composerCard.implicitHeight + Theme.s4 * 2

            // Slash Playbook Autocomplete Popup
            Popup {
                id: slashPopup
                x: composerCard.x
                y: composerCard.y - height - Theme.s2
                width: Math.min(460, composerCard.width)
                padding: Theme.s1
                visible: composerInput.text.indexOf("/") === 0 && composerInput.text.indexOf(" ") === -1 && composerInput.activeFocus

                background: Rectangle {
                    radius: Theme.radiusLg
                    color: Theme.bgSurface
                    border.color: Theme.borderStrong
                    border.width: 1
                }

                contentItem: ColumnLayout {
                    spacing: 2

                    Repeater {
                        model: Workbench.playbooksModel

                        delegate: Rectangle {
                            id: slashItem
                            required property string command
                            required property string title
                            required property string description

                            readonly property bool matches: command.toLowerCase().indexOf(composerInput.text.toLowerCase()) === 0

                            visible: matches
                            Layout.fillWidth: true
                            implicitHeight: visible ? slashRow.implicitHeight + Theme.s2 : 0
                            radius: Theme.radiusSm
                            color: slashHover.hovered ? Theme.bgHover : "transparent"

                            HoverHandler { id: slashHover }
                            TapHandler {
                                onTapped: root.insertPlaybook(slashItem.command + " ")
                            }

                            RowLayout {
                                id: slashRow
                                anchors.fill: parent
                                anchors.margins: Theme.s2
                                spacing: Theme.s2

                                Text {
                                    text: slashItem.command
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textSm
                                    font.weight: Theme.weightSemiBold
                                    color: Theme.accent
                                }

                                Text {
                                    text: slashItem.title
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textSm
                                    font.weight: Theme.weightMedium
                                    color: Theme.textPrimary
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: slashItem.description
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }

            Rectangle {
                id: composerCard
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: Theme.s4
                width: Math.min(parent.width - Theme.s4 * 2, 860)
                implicitHeight: composerCol.implicitHeight + Theme.s3 * 2
                radius: Theme.radiusXl
                color: Theme.bgSurface
                border.color: composerInput.activeFocus ? Theme.accent : Theme.borderStrong
                border.width: 1

                Behavior on border.color { ColorAnimation { duration: Theme.animFast } }

                ColumnLayout {
                    id: composerCol
                    anchors.fill: parent
                    anchors.margins: Theme.s3
                    spacing: Theme.s2

                    ScrollView {
                        Layout.fillWidth: true
                        Layout.preferredHeight: Math.min(140, Math.max(40, composerInput.contentHeight))
                        clip: true
                        ScrollBar.vertical: AppScrollBar {}
                        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                        TextArea {
                            id: composerInput
                            width: parent.width
                            placeholderText: qsTr("Ask MCU Harness Agent or type / for embedded playbooks (Enter to send, Shift+Enter for newline)...")
                            placeholderTextColor: Theme.textMuted
                            color: Theme.textPrimary
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textMd
                            selectionColor: Theme.accentSubtle
                            selectedTextColor: Theme.textPrimary
                            wrapMode: TextArea.Wrap
                            selectByMouse: true
                            padding: Theme.s1
                            background: Item {}

                            Keys.onPressed: function(event) {
                                if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                        && !(event.modifiers & Qt.ShiftModifier)) {
                                    event.accepted = true
                                    root.sendCurrentPrompt()
                                }
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.s2

                        // Model chip
                        Rectangle {
                            implicitWidth: modelChipRow.implicitWidth + Theme.s3
                            implicitHeight: 26
                            radius: Theme.radiusPill
                            color: modelChipHover.hovered ? Theme.bgHover : Theme.bgElevated
                            border.color: Theme.border
                            border.width: 1

                            HoverHandler { id: modelChipHover }
                            TapHandler { onTapped: root.openSettingsRequested("model") }

                            RowLayout {
                                id: modelChipRow
                                anchors.centerIn: parent
                                spacing: Theme.s1 + 2

                                Icon { name: Icons.sparkles; size: 12; color: Theme.accent }
                                Text {
                                    text: (Workbench.provider || "mock") + " · " + (Workbench.model || "mock-model")
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textPrimary
                                }
                            }
                        }

                        // Interlock mode chip
                        Rectangle {
                            implicitWidth: lockChipRow.implicitWidth + Theme.s3
                            implicitHeight: 26
                            radius: Theme.radiusPill
                            color: lockChipHover.hovered ? Theme.bgHover : Theme.bgElevated
                            border.color: Theme.border
                            border.width: 1

                            HoverHandler { id: lockChipHover }
                            TapHandler { onTapped: root.cycleInterlockMode() }

                            RowLayout {
                                id: lockChipRow
                                anchors.centerIn: parent
                                spacing: Theme.s1 + 2

                                Icon {
                                    name: (Workbench.interlockMode === "read_only") ? Icons.lock : (Workbench.interlockMode === "auto_safe" ? Icons.shieldCheck : Icons.shieldAlert)
                                    size: 12
                                    color: (Workbench.interlockMode === "read_only") ? Theme.info : (Workbench.interlockMode === "auto_safe" ? Theme.success : Theme.warning)
                                }
                                Text {
                                    text: Workbench.interlockMode || "interactive"
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textSecondary
                                }
                            }
                        }

                        Item { Layout.fillWidth: true }

                        IconButton {
                            buttonSize: 28
                            iconSize: 15
                            iconName: Icons.messageSquarePlus
                            tooltipText: qsTr("New Session")
                            shortcutText: "Ctrl+N"
                            onClicked: Workbench.newSession()
                        }

                        // Send / Stop morph button
                        AppButton {
                            buttonSize: "sm"
                            variant: Workbench.turnRunning ? "danger" : "primary"
                            iconName: Workbench.turnRunning ? Icons.stop : Icons.arrowUp
                            text: Workbench.turnRunning ? qsTr("Stop") : qsTr("Send")
                            enabled: Workbench.turnRunning || composerInput.text.trim().length > 0
                            onClicked: {
                                if (Workbench.turnRunning) Workbench.abortTurn()
                                else root.sendCurrentPrompt()
                            }
                        }
                    }
                }
            }
        }
    }
}
