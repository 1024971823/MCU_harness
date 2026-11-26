import QtQuick
import QtQuick.Controls.Basic
import McuStudio

Rectangle {
    id: root
    property alias text: area.text
    property alias placeholderText: area.placeholderText
    property alias readOnly: area.readOnly
    property alias textArea: area
    property bool monospace: false

    implicitWidth: 320
    implicitHeight: 96
    radius: Theme.radiusMd
    color: root.enabled ? Theme.bgElevated : Theme.bgSidebar
    border.color: area.activeFocus ? Theme.accent : (hoverArea.hovered ? Theme.borderStrong : Theme.border)
    border.width: 1

    Behavior on border.color { ColorAnimation { duration: Theme.animFast } }
    HoverHandler { id: hoverArea }

    Rectangle {
        anchors.fill: parent
        anchors.margins: -3
        radius: Theme.radiusMd + 2
        color: "transparent"
        border.color: Theme.accent
        border.width: Theme.focusRingWidth
        visible: area.activeFocus && (area.focusReason === Qt.TabFocusReason || area.focusReason === Qt.BacktabFocusReason)
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        anchors.margins: 1
        clip: true
        ScrollBar.vertical: AppScrollBar {}
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        TextArea {
            id: area
            width: scroll.availableWidth
            font.family: root.monospace ? Theme.fontMono : Theme.fontSans
            font.pixelSize: Theme.textBase
            color: root.enabled ? Theme.textPrimary : Theme.textMuted
            placeholderTextColor: Theme.textMuted
            selectionColor: Theme.accentSubtle
            selectedTextColor: Theme.textPrimary
            wrapMode: TextArea.Wrap
            selectByMouse: true
            padding: Theme.s2 + 2
            background: Item {}
        }
    }
}
