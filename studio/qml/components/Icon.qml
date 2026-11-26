import QtQuick
import McuStudio

Item {
    id: root
    property string name: ""
    property color color: Theme.textPrimary
    property int size: 16
    property bool spin: false

    implicitWidth: size
    implicitHeight: size

    Image {
        id: img
        anchors.fill: parent
        source: Icons.url(root.name, root.color)
        sourceSize: Qt.size(root.size, root.size)
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
        asynchronous: false

        RotationAnimation on rotation {
            from: 0
            to: 360
            duration: 900
            loops: Animation.Infinite
            running: root.spin && root.visible
        }
    }
}
