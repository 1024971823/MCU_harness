import QtQuick
import QtQuick.Layouts
import McuStudio

RowLayout {
    id: root
    property string title: ""
    property string subtitle: ""
    property string iconName: ""
    default property alias actions: actionRow.data

    Layout.fillWidth: true
    spacing: Theme.s3

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.s2 + 2

        IconChip {
            visible: root.iconName.length > 0
            icon: root.iconName
            hue: Theme.accent
            size: 28
            iconSize: 15
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            Text {
                Layout.fillWidth: true
                text: root.title
                font.family: Theme.fontSans
                font.pixelSize: Theme.textLg
                font.weight: Theme.weightSemiBold
                color: Theme.textPrimary
                elide: Text.ElideRight
            }

            Text {
                visible: root.subtitle.length > 0
                Layout.fillWidth: true
                text: root.subtitle
                font.family: Theme.fontSans
                font.pixelSize: Theme.textSm
                color: Theme.textSecondary
                wrapMode: Text.WordWrap
            }
        }
    }

    RowLayout {
        id: actionRow
        spacing: Theme.s2
    }
}
