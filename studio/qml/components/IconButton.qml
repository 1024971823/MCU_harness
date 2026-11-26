import QtQuick
import QtQuick.Controls.Basic
import McuStudio

Button {
    id: root
    property string iconName: ""
    property int iconSize: 16
    property int buttonSize: 30
    property bool active: false
    property bool danger: false
    property color iconColor: danger ? Theme.danger : (active ? Theme.accent : (hovered ? Theme.textPrimary : Theme.textSecondary))
    property string tooltipText: ""
    property string shortcutText: ""

    implicitWidth: buttonSize
    implicitHeight: buttonSize
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true

    background: Rectangle {
        radius: Theme.radiusMd
        color: {
            if (!root.enabled) return "transparent"
            if (root.down) return Theme.bgActive
            if (root.active) return Theme.accentSubtle
            if (root.hovered) return Theme.bgHover
            return "transparent"
        }

        Behavior on color { ColorAnimation { duration: Theme.animFast } }

        Rectangle {
            anchors.fill: parent
            anchors.margins: -2
            radius: Theme.radiusMd + 2
            color: "transparent"
            border.color: Theme.accent
            border.width: Theme.focusRingWidth
            visible: root.visualFocus
        }
    }

    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            name: root.iconName
            size: root.iconSize
            color: root.enabled ? root.iconColor : Theme.textMuted
        }
    }

    AppToolTip {
        visible: root.tooltipText.length > 0 && root.hovered && !root.pressed
        text: root.tooltipText
        shortcut: root.shortcutText
    }
}
