import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio

ColumnLayout {
    id: root
    spacing: Theme.s1

    property alias text: input.text
    property alias placeholderText: input.placeholderText
    property alias readOnly: input.readOnly
    property alias validator: input.validator
    property alias maximumLength: input.maximumLength
    property alias inputField: input
    property string leadingIcon: ""
    property bool clearable: false
    property bool passwordField: false
    property bool monospace: false
    property string errorText: ""
    property bool _revealed: false

    signal accepted()
    signal textEdited()

    implicitWidth: 240
    implicitHeight: fieldBox.implicitHeight + (errorLabel.visible ? errorLabel.implicitHeight + Theme.s1 : 0)

    Rectangle {
        id: fieldBox
        Layout.fillWidth: true
        implicitHeight: 34
        radius: Theme.radiusMd + 2
        color: root.enabled ? Theme.bgElevated : Theme.bgSidebar
        border.color: {
            if (root.errorText.length > 0) return Theme.danger
            if (input.activeFocus) return Theme.accent
            if (hoverArea.hovered) return Theme.borderStrong
            return Theme.border
        }
        border.width: 1

        Behavior on border.color {
            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
        }

        HoverHandler { id: hoverArea }

        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            radius: fieldBox.radius + 2
            color: "transparent"
            border.color: Theme.alpha(Theme.accent, 0.45)
            border.width: Theme.focusRingWidth
            visible: input.activeFocus && (input.focusReason === Qt.TabFocusReason || input.focusReason === Qt.BacktabFocusReason)
        }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.s2 + 3
            anchors.rightMargin: Theme.s2
            spacing: Theme.s2

            Icon {
                visible: root.leadingIcon.length > 0
                name: root.leadingIcon
                size: 14
                color: input.activeFocus ? Theme.accent : Theme.textMuted
            }

            TextField {
                id: input
                Layout.fillWidth: true
                Layout.fillHeight: true
                verticalAlignment: TextInput.AlignVCenter
                font.family: root.monospace ? Theme.fontMono : Theme.fontSans
                font.pixelSize: Theme.textBase
                color: root.enabled ? Theme.textPrimary : Theme.textMuted
                placeholderTextColor: Theme.textMuted
                selectionColor: Theme.accentSubtle
                selectedTextColor: Theme.textPrimary
                echoMode: (root.passwordField && !root._revealed) ? TextInput.Password : TextInput.Normal
                selectByMouse: true
                background: Item {}
                padding: 0
                leftPadding: 0
                rightPadding: 0

                onAccepted: root.accepted()
                onTextEdited: root.textEdited()
            }

            IconButton {
                visible: root.passwordField
                buttonSize: 22
                iconSize: 14
                iconName: root._revealed ? Icons.eyeOff : Icons.eye
                tooltipText: root._revealed ? qsTr("Hide") : qsTr("Show")
                onClicked: root._revealed = !root._revealed
            }

            IconButton {
                visible: root.clearable && input.text.length > 0 && !input.readOnly
                buttonSize: 22
                iconSize: 13
                iconName: Icons.close
                tooltipText: qsTr("Clear")
                onClicked: {
                    input.text = ""
                    root.textEdited()
                }
            }
        }
    }

    Text {
        id: errorLabel
        visible: root.errorText.length > 0
        Layout.fillWidth: true
        text: root.errorText
        font.family: Theme.fontSans
        font.pixelSize: Theme.textXs
        color: Theme.danger
        wrapMode: Text.WordWrap
    }
}
