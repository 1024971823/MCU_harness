import QtQuick
import QtQuick.Controls.Basic
import McuStudio

ScrollBar {
    id: root
    implicitWidth: orientation === Qt.Vertical ? 8 : 100
    implicitHeight: orientation === Qt.Horizontal ? 8 : 100
    padding: 1
    policy: ScrollBar.AsNeeded

    readonly property bool activeVisible: root.size < 1.0 && (root.hovered || root.pressed || root.active)

    contentItem: Rectangle {
        implicitWidth: 6
        implicitHeight: 6
        radius: 3
        color: root.pressed ? Theme.textSecondary : (root.hovered ? Theme.textMuted : Theme.borderStrong)
        opacity: root.activeVisible ? 0.85 : 0.0

        Behavior on opacity {
            NumberAnimation { duration: Theme.animNormal; easing.type: Theme.easingType }
        }
        Behavior on color {
            ColorAnimation { duration: Theme.animFast }
        }
    }

    background: Item {}
}
