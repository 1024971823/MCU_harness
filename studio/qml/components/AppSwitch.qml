import QtQuick
import QtQuick.Controls.Basic
import McuStudio

Switch {
    id: root
    implicitWidth: 40
    implicitHeight: 22
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true

    indicator: Rectangle {
        implicitWidth: 40
        implicitHeight: 22
        radius: 11
        y: (root.height - height) / 2
        color: {
            if (!root.enabled) return Theme.bgElevated
            if (root.checked) return root.hovered ? Theme.accentHover : Theme.accent
            return root.hovered ? Theme.bgActive : Theme.bgElevated
        }
        border.color: root.checked ? Theme.alpha("#FFFFFF", 0.14) : Theme.borderStrong
        border.width: 1

        Behavior on color {
            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
        }

        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            radius: 14
            color: "transparent"
            border.color: Theme.accent
            border.width: Theme.focusRingWidth
            visible: root.visualFocus
        }

        Rectangle {
            width: 16
            height: 16
            radius: 8
            y: 3
            x: root.checked ? parent.width - width - 3 : 3
            color: root.checked ? Theme.textOnAccent : Theme.textSecondary

            Behavior on x {
                NumberAnimation {
                    duration: Theme.animationsEnabled ? Theme.animFast : 0
                    easing.type: Theme.easingType
                }
            }
        }
    }

    contentItem: Item {}
}
