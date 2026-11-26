import QtQuick
import QtQuick.Layouts
import McuStudio

Rectangle {
    id: root
    property string title: ""
    property string description: ""
    property string keywords: ""
    property string filterText: ""
    default property alias controlData: controlSlot.data

    readonly property bool matchesFilter: {
        var q = filterText.trim().toLowerCase()
        if (q.length === 0) return true
        return title.toLowerCase().indexOf(q) !== -1
            || description.toLowerCase().indexOf(q) !== -1
            || keywords.toLowerCase().indexOf(q) !== -1
    }

    visible: matchesFilter
    Layout.fillWidth: true
    implicitHeight: visible ? row.implicitHeight + Theme.s3 * 2 : 0
    radius: Theme.radiusMd
    color: rowHover.hovered ? Theme.bgElevated : "transparent"

    Behavior on color { ColorAnimation { duration: Theme.animFast } }
    HoverHandler { id: rowHover }

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.leftMargin: Theme.s3
        anchors.rightMargin: Theme.s3
        anchors.topMargin: Theme.s3
        anchors.bottomMargin: Theme.s3
        spacing: Theme.s4

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            Text {
                Layout.fillWidth: true
                text: root.title
                font.family: Theme.fontSans
                font.pixelSize: Theme.textBase
                font.weight: Theme.weightMedium
                color: Theme.textPrimary
                wrapMode: Text.WordWrap
            }

            Text {
                visible: root.description.length > 0
                Layout.fillWidth: true
                text: root.description
                font.family: Theme.fontSans
                font.pixelSize: Theme.textSm
                color: Theme.textSecondary
                wrapMode: Text.WordWrap
            }
        }

        RowLayout {
            id: controlSlot
            Layout.alignment: Qt.AlignVCenter | Qt.AlignRight
            spacing: Theme.s2
        }
    }
}
