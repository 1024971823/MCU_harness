pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    property string level: "info"
    property string text: ""
    property string timestamp: ""

    readonly property color hue: {
        var lvl = String(level || "").toLowerCase()
        if (lvl === "error") return Theme.hueRose
        if (lvl === "warning" || lvl === "warn") return Theme.hueAmber
        return Theme.hueSky
    }

    readonly property string iconName: {
        var lvl = String(level || "").toLowerCase()
        if (lvl === "error") return Icons.circleAlert
        if (lvl === "warning" || lvl === "warn") return Icons.triangleAlert
        return Icons.info
    }

    readonly property string labelText: {
        var lvl = String(level || "").toLowerCase()
        if (lvl === "error") return qsTr("Error")
        if (lvl === "warning" || lvl === "warn") return qsTr("Warning")
        return qsTr("Notice")
    }

    implicitHeight: banner.implicitHeight

    Rectangle {
        id: banner
        anchors.left: parent.left
        anchors.right: parent.right
        implicitHeight: row.implicitHeight + Theme.s2 * 2
        radius: Theme.radiusLg
        color: Theme.alpha(root.hue, Theme.isDark ? 0.11 : 0.09)
        border.color: Theme.alpha(root.hue, 0.34)
        border.width: 1

        RowLayout {
            id: row
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Theme.s3
            anchors.rightMargin: Theme.s3
            spacing: Theme.s2

            Pill {
                compact: true
                hue: root.hue
                icon: root.iconName
                text: root.labelText
            }

            Text {
                Layout.fillWidth: true
                text: root.text
                font.family: Theme.fontSans
                font.pixelSize: Theme.textSm
                color: Theme.textPrimary
                wrapMode: Text.WordWrap
            }

            Text {
                visible: root.timestamp.length > 0
                text: root.timestamp
                font.family: Theme.fontMono
                font.pixelSize: Theme.textXs
                color: Theme.textMuted
            }
        }
    }
}
