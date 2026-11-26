import QtQuick
import QtQuick.Layouts
import McuStudio

Item {
    id: root
    property string iconName: Icons.info
    property string title: ""
    property string description: ""
    property string actionText: ""
    property string actionIcon: ""

    signal actionTriggered()

    implicitWidth: 360
    implicitHeight: col.implicitHeight + Theme.s6 * 2

    ColumnLayout {
        id: col
        anchors.centerIn: parent
        width: Math.min(parent.width - Theme.s6 * 2, 420)
        spacing: Theme.s3

        IconChip {
            Layout.alignment: Qt.AlignHCenter
            size: 48
            iconSize: 22
            chipRadius: 24
            icon: root.iconName
            hue: Theme.accent
        }

        Text {
            Layout.fillWidth: true
            text: root.title
            font.family: Theme.fontSans
            font.pixelSize: Theme.textLg
            font.weight: Theme.weightSemiBold
            color: Theme.textPrimary
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        Text {
            visible: root.description.length > 0
            Layout.fillWidth: true
            text: root.description
            font.family: Theme.fontSans
            font.pixelSize: Theme.textBase
            color: Theme.textSecondary
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
        }

        AppButton {
            visible: root.actionText.length > 0
            Layout.alignment: Qt.AlignHCenter
            Layout.topMargin: Theme.s1
            variant: "primary"
            iconName: root.actionIcon
            text: root.actionText
            onClicked: root.actionTriggered()
        }
    }
}
