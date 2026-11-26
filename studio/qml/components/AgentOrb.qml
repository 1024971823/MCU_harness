import QtQuick
import McuStudio

Item {
    id: root
    property int size: 32
    property bool animated: false
    property bool spinning: false
    property string icon: Icons.sparkles
    property bool squircle: false

    readonly property int coreRadius: squircle ? Math.max(6, Math.round(size * 0.30)) : size / 2

    implicitWidth: size
    implicitHeight: size

    // Soft ambient outer halo
    Rectangle {
        id: halo
        anchors.centerIn: parent
        width: root.size + Math.max(6, Math.round(root.size * 0.22))
        height: width
        radius: root.squircle ? root.coreRadius + 4 : width / 2
        color: Theme.alpha(Theme.brandA, 0.16)
        border.color: Theme.alpha(Theme.brandC, 0.22)
        border.width: 1
        opacity: root.animated ? 0.75 : 0.45

        SequentialAnimation on opacity {
            running: root.animated && root.visible
            loops: Animation.Infinite
            NumberAnimation { from: 0.45; to: 0.90; duration: 1400; easing.type: Easing.InOutSine }
            NumberAnimation { from: 0.90; to: 0.45; duration: 1400; easing.type: Easing.InOutSine }
        }
        SequentialAnimation on scale {
            running: root.animated && root.visible
            loops: Animation.Infinite
            NumberAnimation { from: 0.96; to: 1.08; duration: 1400; easing.type: Easing.InOutSine }
            NumberAnimation { from: 1.08; to: 0.96; duration: 1400; easing.type: Easing.InOutSine }
        }
    }

    // Rotating gradient ring when spinning
    Rectangle {
        id: spinRing
        anchors.centerIn: parent
        width: root.size + 4
        height: width
        radius: root.squircle ? root.coreRadius + 2 : width / 2
        color: "transparent"
        visible: root.spinning
        border.width: 1.5
        border.color: Theme.brandC

        Rectangle {
            width: Math.max(4, Math.round(root.size * 0.18))
            height: width
            radius: width / 2
            color: Theme.brandC
            anchors.top: parent.top
            anchors.topMargin: -1
            anchors.horizontalCenter: parent.horizontalCenter
        }

        RotationAnimation on rotation {
            from: 0
            to: 360
            duration: 2000
            loops: Animation.Infinite
            running: root.spinning && root.visible
        }
    }

    // Core Aurora gradient orb
    Rectangle {
        id: core
        anchors.fill: parent
        radius: root.coreRadius
        border.color: Theme.alpha("#FFFFFF", 0.22)
        border.width: 1

        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: Theme.brandA }
            GradientStop { position: 0.5; color: Theme.brandB }
            GradientStop { position: 1.0; color: Theme.brandC }
        }

        Icon {
            anchors.centerIn: parent
            name: root.icon
            size: Math.max(11, Math.round(root.size * 0.48))
            color: Theme.textOnAccent
        }
    }
}
