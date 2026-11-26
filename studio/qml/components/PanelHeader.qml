import QtQuick
import QtQuick.Layouts
import McuStudio

Rectangle {
    id: root
    property string icon: ""
    property alias iconName: root.icon
    property color hue: Theme.accent
    property string title: ""
    property string subtitle: ""
    property bool maximizable: false
    property bool maximized: false
    property bool closable: true
    default property alias actions: actionsRow.data

    signal maximizeRequested()
    signal closeRequested()

    implicitWidth: 320
    implicitHeight: subtitle.length > 0 ? 52 : 44
    color: Theme.bgPanel

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.s3
        anchors.rightMargin: Theme.s2
        spacing: Theme.s2 + 2

        IconChip {
            visible: root.icon.length > 0
            icon: root.icon
            hue: root.hue
            size: 26
            iconSize: 14
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 1

            Text {
                Layout.fillWidth: true
                text: root.title
                font.family: Theme.fontSans
                font.pixelSize: Theme.textBase
                font.weight: Theme.weightSemiBold
                color: Theme.textPrimary
                elide: Text.ElideRight
            }

            Text {
                visible: root.subtitle.length > 0
                Layout.fillWidth: true
                text: root.subtitle
                font.family: Theme.fontSans
                font.pixelSize: Theme.textXs
                color: Theme.textMuted
                elide: Text.ElideRight
            }
        }

        RowLayout {
            id: actionsRow
            spacing: Theme.s1
        }

        IconButton {
            visible: root.maximizable
            buttonSize: 26
            iconSize: 14
            iconName: root.maximized ? Icons.minimize2 : Icons.maximize2
            tooltipText: root.maximized ? qsTr("Restore") : qsTr("Maximize")
            onClicked: root.maximizeRequested()
        }

        IconButton {
            visible: root.closable
            buttonSize: 26
            iconSize: 14
            iconName: Icons.close
            tooltipText: qsTr("Close")
            onClicked: root.closeRequested()
        }
    }

    Divider {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
    }
}
