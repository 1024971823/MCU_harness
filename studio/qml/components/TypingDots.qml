pragma ComponentBehavior: Bound
import QtQuick
import McuStudio

Row {
    id: root
    property color color: Theme.accent
    property int dotSize: 5
    property bool active: true
    property int dotSpacing: Math.max(3, Math.round(dotSize * 0.7))

    spacing: dotSpacing

    Repeater {
        model: 3

        delegate: Rectangle {
            id: dotItem
            required property int index
            width: root.dotSize
            height: root.dotSize
            radius: root.dotSize / 2
            color: root.color
            opacity: root.active ? 0.35 : 0.55

            SequentialAnimation {
                running: root.active && root.visible
                loops: Animation.Infinite

                PauseAnimation { duration: dotItem.index * 160 }
                ParallelAnimation {
                    NumberAnimation { target: dotItem; property: "opacity"; from: 0.30; to: 1.0; duration: 360; easing.type: Easing.InOutSine }
                    NumberAnimation { target: dotItem; property: "scale"; from: 0.85; to: 1.15; duration: 360; easing.type: Easing.InOutSine }
                }
                ParallelAnimation {
                    NumberAnimation { target: dotItem; property: "opacity"; from: 1.0; to: 0.30; duration: 360; easing.type: Easing.InOutSine }
                    NumberAnimation { target: dotItem; property: "scale"; from: 1.15; to: 0.85; duration: 360; easing.type: Easing.InOutSine }
                }
                PauseAnimation { duration: (2 - dotItem.index) * 160 + 220 }
            }
        }
    }
}
