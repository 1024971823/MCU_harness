import QtQuick
import QtQuick.Controls.Basic
import McuStudio

CheckBox {
    id: root
    implicitWidth: indicator.width + (text.length > 0 ? contentItem.implicitWidth + spacing : 0)
    implicitHeight: Math.max(24, contentItem.implicitHeight)
    spacing: Theme.s2
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true

    indicator: Rectangle {
        implicitWidth: 18
        implicitHeight: 18
        x: root.leftPadding
        y: (root.height - height) / 2
        radius: Theme.radiusSm
        color: root.checked ? Theme.accent : (root.hovered ? Theme.bgHover : Theme.bgElevated)
        border.color: root.checked ? Theme.accent : (root.hovered ? Theme.borderStrong : Theme.border)
        border.width: 1

        Behavior on color { ColorAnimation { duration: Theme.animFast } }

        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            radius: Theme.radiusSm + 2
            color: "transparent"
            border.color: Theme.accent
            border.width: Theme.focusRingWidth
            visible: root.visualFocus
        }

        Icon {
            anchors.centerIn: parent
            visible: root.checked
            name: Icons.check
            size: 12
            color: Theme.textOnAccent
        }
    }

    contentItem: Text {
        leftPadding: root.indicator.width + root.spacing
        text: root.text
        font.family: Theme.fontSans
        font.pixelSize: Theme.textBase
        color: root.enabled ? Theme.textPrimary : Theme.textMuted
        verticalAlignment: Text.AlignVCenter
    }
}
