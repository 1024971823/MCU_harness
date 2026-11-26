import QtQuick
import QtQuick.Layouts
import McuStudio

Rectangle {
    id: root
    property string message: ""
    property string level: "info" // "info" | "success" | "warning" | "danger"
    property string actionText: ""

    signal actionClicked()
    signal closed()

    readonly property color accentColor: {
        switch (level) {
        case "success": return Theme.success
        case "warning": return Theme.warning
        case "danger": return Theme.danger
        default: return Theme.info
        }
    }

    readonly property string levelIcon: {
        switch (level) {
        case "success": return Icons.circleCheck
        case "warning": return Icons.triangleAlert
        case "danger": return Icons.circleAlert
        default: return Icons.info
        }
    }

    implicitWidth: 340
    implicitHeight: row.implicitHeight + Theme.s3 * 2
    radius: Theme.radiusLg
    color: Theme.bgSurface
    border.color: Theme.borderStrong
    border.width: 1

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 3
        topLeftRadius: Theme.radiusLg
        bottomLeftRadius: Theme.radiusLg
        color: root.accentColor
    }

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.leftMargin: Theme.s3 + 3
        anchors.rightMargin: Theme.s2
        anchors.topMargin: Theme.s3
        anchors.bottomMargin: Theme.s3
        spacing: Theme.s2

        Icon {
            name: root.levelIcon
            size: 16
            color: root.accentColor
        }

        Text {
            Layout.fillWidth: true
            text: root.message
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            color: Theme.textPrimary
            wrapMode: Text.WordWrap
        }

        AppButton {
            visible: root.actionText.length > 0
            buttonSize: "sm"
            variant: "ghost"
            text: root.actionText
            onClicked: root.actionClicked()
        }

        IconButton {
            buttonSize: 24
            iconSize: 13
            iconName: Icons.close
            onClicked: root.closed()
        }
    }
}
