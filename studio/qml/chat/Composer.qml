pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root

    signal sendRequested(string text)
    signal openSettingsRequested(string section)

    property alias text: inputArea.text

    function focusInput() {
        inputArea.forceActiveFocus()
    }

    function insertSkill(cmd) {
        var c = String(cmd || "").trim()
        if (c.length === 0) return
        skillPicker.close()
        inputArea.text = c + " "
        inputArea.cursorPosition = inputArea.text.length
        inputArea.forceActiveFocus()
    }

    function sendCurrent() {
        var trimmed = inputArea.text.trim()
        if (trimmed.length === 0 || Workbench.turnRunning) return
        skillPicker.close()
        root.sendRequested(trimmed)
        inputArea.text = ""
    }

    function cycleInterlockMode() {
        var modes = ["read_only", "interactive", "auto_safe"]
        var idx = modes.indexOf(Workbench.interlockMode || "interactive")
        var next = modes[(idx + 1) % modes.length]
        Workbench.setInterlockMode(next)
    }

    readonly property color interlockHue: {
        var m = Workbench.interlockMode || "interactive"
        if (m === "read_only") return Theme.hueEmerald
        if (m === "auto_safe") return Theme.hueRose
        return Theme.hueAmber
    }

    readonly property string interlockIcon: {
        var m = Workbench.interlockMode || "interactive"
        if (m === "read_only") return Icons.lock
        if (m === "auto_safe") return Icons.shieldCheck
        return Icons.shieldAlert
    }

    readonly property string interlockLabel: {
        var m = Workbench.interlockMode || "interactive"
        if (m === "read_only") return qsTr("Read-only")
        if (m === "auto_safe") return qsTr("Auto-safe")
        return qsTr("Interactive")
    }

    readonly property bool canSend: !Workbench.turnRunning && inputArea.text.trim().length > 0

    implicitHeight: cardCol.implicitHeight + Theme.s3 * 2 + 2

    SkillPicker {
        id: skillPicker
        x: outerCard.x
        y: outerCard.y - height - Theme.s2
        width: Math.min(520, outerCard.width)
        filterText: inputArea.text.indexOf("/") === 0 ? inputArea.text : ""
        onSkillSelected: function(cmd) {
            root.insertSkill(cmd)
        }
    }

    // Outer card: 1px Theme.border normally, brand gradient border on focus
    Rectangle {
        id: outerCard
        anchors.fill: parent
        radius: Theme.composerRadius
        color: inputArea.activeFocus ? "transparent" : (cardHover.hovered ? Theme.borderStrong : Theme.border)
        gradient: inputArea.activeFocus ? focusGradient : null

        Behavior on color {
            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
        }

        HoverHandler {
            id: cardHover
        }

        Gradient {
            id: focusGradient
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: Theme.brandA }
            GradientStop { position: 0.5; color: Theme.brandB }
            GradientStop { position: 1.0; color: Theme.brandC }
        }

        Rectangle {
            id: innerCard
            anchors.fill: parent
            anchors.margins: 1
            radius: Theme.composerRadius - 1
            color: Theme.bgSurface

            ColumnLayout {
                id: cardCol
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Theme.s3
                spacing: Theme.s2

                ScrollView {
                    id: inputScroll
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(160, Math.max(40, inputArea.contentHeight))
                    clip: true
                    ScrollBar.vertical: AppScrollBar {}
                    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                    TextArea {
                        id: inputArea
                        width: inputScroll.availableWidth
                        placeholderText: qsTr("Ask MCU Harness Agent or type / for skills (Enter to send, Shift+Enter for newline)…")
                        placeholderTextColor: Theme.textMuted
                        color: Theme.textPrimary
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.chatTextSize
                        selectionColor: Theme.accentSubtle
                        selectedTextColor: Theme.textPrimary
                        wrapMode: TextArea.Wrap
                        selectByMouse: true
                        padding: Theme.s1
                        background: Item {}

                        onTextChanged: {
                            var t = inputArea.text
                            if (t.indexOf("/") === 0 && t.indexOf(" ") === -1 && t.indexOf("\n") === -1 && inputArea.activeFocus) {
                                if (!skillPicker.opened) {
                                    skillPicker.open()
                                }
                            } else if (skillPicker.opened && t.indexOf("/") !== 0) {
                                skillPicker.close()
                            }
                        }

                        Keys.onPressed: function(event) {
                            if (skillPicker.opened) {
                                if (event.key === Qt.Key_Up) {
                                    event.accepted = true
                                    skillPicker.moveUp()
                                    return
                                }
                                if (event.key === Qt.Key_Down) {
                                    event.accepted = true
                                    skillPicker.moveDown()
                                    return
                                }
                                if (event.key === Qt.Key_Escape) {
                                    event.accepted = true
                                    skillPicker.close()
                                    return
                                }
                                if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Tab)
                                        && !(event.modifiers & Qt.ShiftModifier)) {
                                    if (skillPicker.acceptCurrent()) {
                                        event.accepted = true
                                        return
                                    }
                                }
                            }
                            if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                    && !(event.modifiers & Qt.ShiftModifier)) {
                                event.accepted = true
                                root.sendCurrent()
                            }
                        }
                    }
                }

                // Bottom toolbar
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    // Skills button
                    Chip {
                        id: skillsBtn
                        icon: Icons.sparkles
                        text: "/"
                        monospace: true
                        checkable: false
                        active: skillPicker.opened
                        hue: Theme.accent
                        tooltipText: qsTr("Skills & slash commands")
                        onClicked: {
                            if (skillPicker.opened) {
                                skillPicker.close()
                            } else {
                                skillPicker.open()
                            }
                        }
                    }

                    // Model pill
                    Pill {
                        clickable: true
                        subtle: false
                        icon: Icons.cpu
                        hue: Theme.hueIndigo
                        text: (Workbench.provider || "mock") + " · " + (Workbench.model || "mock-model")
                        tooltipText: qsTr("Configure LLM provider & model")
                        onClicked: root.openSettingsRequested("model")
                    }

                    // Thinking toggle
                    IconButton {
                        buttonSize: 28
                        iconSize: 15
                        iconName: Icons.brain
                        active: Settings.showThinking
                        iconColor: Settings.showThinking ? Theme.hueViolet : Theme.textMuted
                        tooltipText: Settings.showThinking ? qsTr("Hide thinking chain") : qsTr("Show thinking chain")
                        onClicked: Settings.setShowThinking(!Settings.showThinking)
                    }

                    // Interlock pill
                    Pill {
                        clickable: true
                        dot: true
                        hue: root.interlockHue
                        icon: root.interlockIcon
                        text: root.interlockLabel
                        tooltipText: qsTr("Click to cycle safety interlock mode")
                        onClicked: root.cycleInterlockMode()
                    }

                    Item { Layout.fillWidth: true }

                    // Round 36px Send / Stop crossfade button
                    Item {
                        id: actionBtn
                        Layout.preferredWidth: 36
                        Layout.preferredHeight: 36

                        readonly property bool interactive: Workbench.turnRunning || root.canSend

                        HoverHandler {
                            id: sendHover
                            enabled: actionBtn.interactive
                            cursorShape: actionBtn.interactive ? Qt.PointingHandCursor : Qt.ArrowCursor
                        }

                        TapHandler {
                            enabled: actionBtn.interactive
                            onTapped: {
                                if (Workbench.turnRunning) {
                                    Workbench.abortTurn()
                                } else {
                                    root.sendCurrent()
                                }
                            }
                        }

                        // Send visual (gradient circle)
                        Rectangle {
                            id: sendCircle
                            anchors.fill: parent
                            radius: 18
                            opacity: Workbench.turnRunning ? 0.0 : (root.canSend ? 1.0 : 0.45)
                            color: root.canSend ? "transparent" : Theme.bgElevated
                            border.color: root.canSend ? Theme.alpha("#FFFFFF", 0.16) : Theme.border
                            border.width: 1
                            gradient: root.canSend ? sendGrad : null

                            Behavior on opacity {
                                NumberAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                            }

                            Gradient {
                                id: sendGrad
                                orientation: Gradient.Horizontal
                                GradientStop { position: 0.0; color: Theme.brandA }
                                GradientStop { position: 0.5; color: Theme.brandB }
                                GradientStop { position: 1.0; color: Theme.brandC }
                            }

                            Rectangle {
                                anchors.fill: parent
                                radius: 18
                                color: (root.canSend && sendHover.hovered) ? "#20FFFFFF" : "transparent"
                            }

                            Icon {
                                anchors.centerIn: parent
                                name: Icons.arrowUp
                                size: 17
                                color: root.canSend ? Theme.textOnAccent : Theme.textMuted
                            }
                        }

                        // Stop visual (rose circle)
                        Rectangle {
                            id: stopCircle
                            anchors.fill: parent
                            radius: 18
                            opacity: Workbench.turnRunning ? 1.0 : 0.0
                            color: sendHover.hovered ? Qt.lighter(Theme.hueRose, 1.08) : Theme.hueRose
                            border.color: Theme.alpha("#FFFFFF", 0.18)
                            border.width: 1

                            Behavior on opacity {
                                NumberAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                            }

                            Icon {
                                anchors.centerIn: parent
                                name: Icons.square
                                size: 14
                                color: Theme.textOnAccent
                            }
                        }

                        AppToolTip {
                            visible: sendHover.hovered
                            text: Workbench.turnRunning ? qsTr("Stop generation") : qsTr("Send message")
                            shortcut: Workbench.turnRunning ? "Esc" : "Enter"
                        }
                    }
                }
            }
        }
    }
}
