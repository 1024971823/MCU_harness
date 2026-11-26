import QtQuick
import QtQuick.Layouts
import McuStudio

Rectangle {
    id: root
    property string title: ""
    property string subtitle: ""
    property string iconName: ""
    property int cardPadding: Theme.s4
    property color borderColor: Theme.borderSubtle
    property color fillColor: Theme.bgSurface
    default property alias content: bodyCol.data
    property alias headerActions: headerRow.data

    implicitWidth: 320
    implicitHeight: layout.implicitHeight + cardPadding * 2
    radius: Theme.radiusLg + 2
    color: fillColor
    border.color: borderColor
    border.width: 1

    Behavior on border.color {
        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
    }

    ColumnLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: root.cardPadding
        spacing: Theme.s3

        RowLayout {
            id: headerBar
            visible: root.title.length > 0 || root.subtitle.length > 0 || headerRow.children.length > 0
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
                    visible: root.title.length > 0
                    Layout.fillWidth: true
                    text: root.title
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textMd
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

            RowLayout {
                id: headerRow
                spacing: Theme.s2
            }
        }

        ColumnLayout {
            id: bodyCol
            Layout.fillWidth: true
            spacing: Theme.s2
        }
    }
}
