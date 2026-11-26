import QtQuick
import McuStudio

Rectangle {
    id: root
    property string icon: ""
    property alias iconName: root.icon
    property color hue: Theme.accent
    property int size: 32
    property int iconSize: Math.max(12, Math.round(size * 0.5))
    property int chipRadius: Math.min(Theme.radiusLg, Math.round(size * 0.28))
    property bool bordered: true
    property real fillAlpha: Theme.isDark ? 0.14 : 0.11
    property real borderAlpha: Theme.isDark ? 0.28 : 0.24

    implicitWidth: size
    implicitHeight: size
    radius: chipRadius
    color: Theme.alpha(hue, fillAlpha)
    border.color: bordered ? Theme.alpha(hue, borderAlpha) : "transparent"
    border.width: bordered ? 1 : 0

    Behavior on color {
        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
    }
    Behavior on border.color {
        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
    }

    Icon {
        anchors.centerIn: parent
        visible: root.icon.length > 0
        name: root.icon
        size: root.iconSize
        color: root.hue
    }
}
