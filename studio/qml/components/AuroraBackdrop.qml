import QtQuick
import QtQuick.Effects
import McuStudio

Item {
    id: root
    property bool animated: true
    property real intensity: 1.0

    clip: true

    Component {
        id: auroraBlurEffect
        MultiEffect {
            blurEnabled: true
            blur: 1.0
            blurMax: 64
        }
    }

    Item {
        id: blobLayer
        anchors.fill: parent
        layer.enabled: Theme.effectsEnabled
        layer.effect: Theme.effectsEnabled ? auroraBlurEffect : null

        // Blob 1: Violet (top-left)
        Item {
            id: blobViolet
            width: Math.max(360, root.width * 0.48)
            height: width
            x: root.width * 0.08
            y: root.height * 0.04

            Rectangle {
                anchors.centerIn: parent
                width: parent.width
                height: parent.height
                radius: width / 2
                color: Theme.hueViolet
                opacity: (Theme.effectsEnabled ? 0.14 : 0.04) * root.intensity
            }
            Rectangle {
                visible: !Theme.effectsEnabled
                anchors.centerIn: parent
                width: parent.width * 0.68
                height: width
                radius: width / 2
                color: Theme.hueViolet
                opacity: 0.05 * root.intensity
            }
            Rectangle {
                visible: !Theme.effectsEnabled
                anchors.centerIn: parent
                width: parent.width * 0.38
                height: width
                radius: width / 2
                color: Theme.hueViolet
                opacity: 0.05 * root.intensity
            }

            SequentialAnimation on x {
                running: root.animated && root.visible && Theme.animationsEnabled
                loops: Animation.Infinite
                NumberAnimation { from: root.width * 0.06; to: root.width * 0.18; duration: 15000; easing.type: Easing.InOutSine }
                NumberAnimation { from: root.width * 0.18; to: root.width * 0.06; duration: 15000; easing.type: Easing.InOutSine }
            }
            SequentialAnimation on y {
                running: root.animated && root.visible && Theme.animationsEnabled
                loops: Animation.Infinite
                NumberAnimation { from: root.height * 0.02; to: root.height * 0.14; duration: 17000; easing.type: Easing.InOutSine }
                NumberAnimation { from: root.height * 0.14; to: root.height * 0.02; duration: 17000; easing.type: Easing.InOutSine }
            }
        }

        // Blob 2: Indigo (center-right)
        Item {
            id: blobIndigo
            width: Math.max(380, root.width * 0.50)
            height: width
            x: root.width * 0.42
            y: root.height * 0.12

            Rectangle {
                anchors.centerIn: parent
                width: parent.width
                height: parent.height
                radius: width / 2
                color: Theme.hueIndigo
                opacity: (Theme.effectsEnabled ? 0.13 : 0.04) * root.intensity
            }
            Rectangle {
                visible: !Theme.effectsEnabled
                anchors.centerIn: parent
                width: parent.width * 0.68
                height: width
                radius: width / 2
                color: Theme.hueIndigo
                opacity: 0.05 * root.intensity
            }
            Rectangle {
                visible: !Theme.effectsEnabled
                anchors.centerIn: parent
                width: parent.width * 0.38
                height: width
                radius: width / 2
                color: Theme.hueIndigo
                opacity: 0.05 * root.intensity
            }

            SequentialAnimation on x {
                running: root.animated && root.visible && Theme.animationsEnabled
                loops: Animation.Infinite
                NumberAnimation { from: root.width * 0.46; to: root.width * 0.32; duration: 16500; easing.type: Easing.InOutSine }
                NumberAnimation { from: root.width * 0.32; to: root.width * 0.46; duration: 16500; easing.type: Easing.InOutSine }
            }
            SequentialAnimation on y {
                running: root.animated && root.visible && Theme.animationsEnabled
                loops: Animation.Infinite
                NumberAnimation { from: root.height * 0.14; to: root.height * 0.04; duration: 14000; easing.type: Easing.InOutSine }
                NumberAnimation { from: root.height * 0.04; to: root.height * 0.14; duration: 14000; easing.type: Easing.InOutSine }
            }
        }

        // Blob 3: Cyan (lower center)
        Item {
            id: blobCyan
            width: Math.max(320, root.width * 0.42)
            height: width
            x: root.width * 0.24
            y: root.height * 0.34

            Rectangle {
                anchors.centerIn: parent
                width: parent.width
                height: parent.height
                radius: width / 2
                color: Theme.hueCyan
                opacity: (Theme.effectsEnabled ? 0.11 : 0.03) * root.intensity
            }
            Rectangle {
                visible: !Theme.effectsEnabled
                anchors.centerIn: parent
                width: parent.width * 0.68
                height: width
                radius: width / 2
                color: Theme.hueCyan
                opacity: 0.04 * root.intensity
            }
            Rectangle {
                visible: !Theme.effectsEnabled
                anchors.centerIn: parent
                width: parent.width * 0.38
                height: width
                radius: width / 2
                color: Theme.hueCyan
                opacity: 0.04 * root.intensity
            }

            SequentialAnimation on x {
                running: root.animated && root.visible && Theme.animationsEnabled
                loops: Animation.Infinite
                NumberAnimation { from: root.width * 0.20; to: root.width * 0.34; duration: 18000; easing.type: Easing.InOutSine }
                NumberAnimation { from: root.width * 0.34; to: root.width * 0.20; duration: 18000; easing.type: Easing.InOutSine }
            }
            SequentialAnimation on y {
                running: root.animated && root.visible && Theme.animationsEnabled
                loops: Animation.Infinite
                NumberAnimation { from: root.height * 0.36; to: root.height * 0.24; duration: 15500; easing.type: Easing.InOutSine }
                NumberAnimation { from: root.height * 0.24; to: root.height * 0.36; duration: 15500; easing.type: Easing.InOutSine }
            }
        }
    }
}
