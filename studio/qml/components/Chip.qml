import QtQuick
import QtQuick.Layouts
import McuStudio

Rectangle {
    id: root
    property string text: ""
    property string icon: ""
    property alias iconName: root.icon
    property string trailingIcon: ""
    property color hue: Theme.accent
    property bool active: false
    property bool checkable: true
    property bool monospace: false
    property string tooltipText: ""

    signal clicked()
    signal toggled(bool active)

    implicitWidth: row.implicitWidth + Theme.s3 * 2
    implicitHeight: 28
    radius: Theme.radiusPill
    activeFocusOnTab: true

    color: {
        if (active) return Theme.alpha(hue, hoverHandler.hovered ? (Theme.isDark ? 0.22 : 0.16) : (Theme.isDark ? 0.16 : 0.12))
        if (hoverHandler.hovered) return Theme.bgHover
        return Theme.bgElevated
    }
    border.color: {
        if (active) return Theme.alpha(hue, 0.50)
        if (hoverHandler.hovered) return Theme.borderStrong
        return Theme.border
    }
    border.width: 1

    Behavior on color {
        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
    }
    Behavior on border.color {
        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
    }

    function activate() {
        if (checkable) {
            active = !active
            toggled(active)
        }
        clicked()
    }

    Keys.onReturnPressed: activate()
    Keys.onSpacePressed: activate()

    HoverHandler {
        id: hoverHandler
        cursorShape: Qt.PointingHandCursor
    }

    TapHandler {
        onTapped: root.activate()
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: -3
        radius: Theme.radiusPill
        color: "transparent"
        border.color: Theme.accent
        border.width: Theme.focusRingWidth
        visible: root.activeFocus
    }

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: Theme.s1 + 2

        Icon {
            visible: root.icon.length > 0
            name: root.icon
            size: 13
            color: root.active ? root.hue : (hoverHandler.hovered ? Theme.textPrimary : Theme.textSecondary)
        }

        Text {
            visible: root.text.length > 0
            text: root.text
            font.family: root.monospace ? Theme.fontMono : Theme.fontSans
            font.pixelSize: Theme.textSm
            font.weight: root.active ? Theme.weightSemiBold : Theme.weightMedium
            color: root.active ? root.hue : (hoverHandler.hovered ? Theme.textPrimary : Theme.textSecondary)
        }

        Icon {
            visible: root.trailingIcon.length > 0
            name: root.trailingIcon
            size: 12
            color: root.active ? root.hue : Theme.textMuted
        }
    }

    AppToolTip {
        visible: root.tooltipText.length > 0 && hoverHandler.hovered
        text: root.tooltipText
    }
}
