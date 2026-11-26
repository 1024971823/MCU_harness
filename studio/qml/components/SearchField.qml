import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio

Rectangle {
    id: root
    property alias text: input.text
    property alias placeholderText: input.placeholderText
    property alias inputField: input
    property string shortcutHint: ""
    property int fieldHeight: 32

    signal accepted()
    signal textEdited()
    signal cleared()

    implicitWidth: 220
    implicitHeight: fieldHeight
    radius: Theme.radiusMd + 2
    color: root.enabled ? Theme.bgElevated : Theme.bgSidebar
    border.color: input.activeFocus ? Theme.accent : (hoverHandler.hovered ? Theme.borderStrong : Theme.border)
    border.width: 1

    Behavior on border.color {
        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
    }

    HoverHandler { id: hoverHandler }

    Rectangle {
        anchors.fill: parent
        anchors.margins: -3
        radius: root.radius + 2
        color: "transparent"
        border.color: Theme.alpha(Theme.accent, 0.45)
        border.width: Theme.focusRingWidth
        visible: input.activeFocus && (input.focusReason === Qt.TabFocusReason || input.focusReason === Qt.BacktabFocusReason)
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.s2 + 2
        anchors.rightMargin: Theme.s2
        spacing: Theme.s2

        Icon {
            name: Icons.search
            size: 14
            color: input.activeFocus ? Theme.accent : Theme.textMuted
        }

        TextField {
            id: input
            Layout.fillWidth: true
            Layout.fillHeight: true
            verticalAlignment: TextInput.AlignVCenter
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            color: root.enabled ? Theme.textPrimary : Theme.textMuted
            placeholderTextColor: Theme.textMuted
            selectionColor: Theme.accentSubtle
            selectedTextColor: Theme.textPrimary
            selectByMouse: true
            background: Item {}
            padding: 0
            leftPadding: 0
            rightPadding: 0

            onAccepted: root.accepted()
            onTextEdited: root.textEdited()
        }

        Kbd {
            visible: root.shortcutHint.length > 0 && input.text.length === 0 && !input.activeFocus
            text: root.shortcutHint
        }

        IconButton {
            visible: input.text.length > 0
            buttonSize: 20
            iconSize: 12
            iconName: Icons.close
            tooltipText: qsTr("Clear")
            onClicked: {
                input.text = ""
                root.textEdited()
                root.cleared()
            }
        }
    }
}
