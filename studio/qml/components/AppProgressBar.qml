import QtQuick
import QtQuick.Controls.Basic
import McuStudio

ProgressBar {
    id: root
    property color fillColor: Theme.accent
    property color trackColor: Theme.bgElevated
    property int barHeight: 6

    implicitWidth: 200
    implicitHeight: barHeight

    background: Rectangle {
        implicitHeight: root.barHeight
        radius: root.barHeight / 2
        color: root.trackColor
        border.color: Theme.borderSubtle
        border.width: 1
    }

    contentItem: Item {
        implicitHeight: root.barHeight

        Rectangle {
            id: fillRect
            height: parent.height
            radius: height / 2
            color: root.fillColor
            visible: !root.indeterminate
            width: Math.max(root.visualPosition > 0 ? height : 0, root.visualPosition * parent.width)

            Behavior on width {
                NumberAnimation { duration: Theme.animNormal; easing.type: Theme.easingType }
            }
            Behavior on color {
                ColorAnimation { duration: Theme.animFast }
            }
        }

        Item {
            anchors.fill: parent
            clip: true
            visible: root.indeterminate

            Rectangle {
                id: indetBar
                width: parent.width * 0.36
                height: parent.height
                radius: height / 2
                color: root.fillColor

                SequentialAnimation on x {
                    running: root.indeterminate && root.visible
                    loops: Animation.Infinite
                    NumberAnimation {
                        from: -indetBar.width
                        to: root.width
                        duration: 1100
                        easing.type: Easing.InOutQuad
                    }
                }
            }
        }
    }
}
