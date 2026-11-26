import QtQuick
import McuStudio

Item {
    id: root
    property color color: Theme.success
    property int size: 8
    property bool pulsing: false

    implicitWidth: size
    implicitHeight: size

    Rectangle {
        id: halo
        anchors.centerIn: parent
        width: root.size * 2
        height: root.size * 2
        radius: width / 2
        color: root.color
        visible: root.pulsing
        opacity: 0.0

        SequentialAnimation on opacity {
            running: root.pulsing && root.visible
            loops: Animation.Infinite
            NumberAnimation { from: 0.45; to: 0.0; duration: 1400; easing.type: Easing.OutCubic }
            PauseAnimation { duration: 300 }
        }
        SequentialAnimation on scale {
            running: root.pulsing && root.visible
            loops: Animation.Infinite
            NumberAnimation { from: 0.6; to: 1.25; duration: 1400; easing.type: Easing.OutCubic }
            PauseAnimation { duration: 300 }
        }
    }

    Rectangle {
        anchors.centerIn: parent
        width: root.size
        height: root.size
        radius: width / 2
        color: root.color
        Behavior on color { ColorAnimation { duration: Theme.animFast } }
    }
}
