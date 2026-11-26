import QtQuick
import QtQuick.Layouts
import McuStudio

Rectangle {
    id: root
    property string text: ""
    property color hue: Theme.accent
    property string icon: ""
    property alias iconName: root.icon
    property bool dot: false
    property bool dotPulsing: false
    property bool clickable: false
    property bool compact: false
    property bool subtle: true
    property string tooltipText: ""

    signal clicked()

    readonly property color activeBg: subtle
        ? Theme.alpha(hue, clickable && hoverHandler.hovered ? (Theme.isDark ? 0.20 : 0.16) : (Theme.isDark ? 0.13 : 0.10))
        : (clickable && hoverHandler.hovered ? Theme.bgHover : Theme.bgElevated)

    readonly property color activeBorder: subtle
        ? Theme.alpha(hue, clickable && hoverHandler.hovered ? 0.45 : 0.30)
        : (clickable && hoverHandler.hovered ? Theme.borderStrong : Theme.border)

    implicitWidth: row.implicitWidth + (compact ? Theme.s2 + 2 : Theme.s3)
    implicitHeight: compact ? 20 : 24
    radius: Theme.radiusPill
    color: activeBg
    border.color: activeBorder
    border.width: 1
    activeFocusOnTab: clickable

    Behavior on color {
        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
    }
    Behavior on border.color {
        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
    }

    Keys.onReturnPressed: if (clickable) clicked()
    Keys.onSpacePressed: if (clickable) clicked()

    HoverHandler {
        id: hoverHandler
        enabled: root.clickable
        cursorShape: root.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor
    }

    TapHandler {
        enabled: root.clickable
        onTapped: root.clicked()
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: -3
        radius: Theme.radiusPill
        color: "transparent"
        border.color: Theme.accent
        border.width: Theme.focusRingWidth
        visible: root.activeFocus && root.clickable
    }

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: Theme.s1 + 1

        StatusDot {
            visible: root.dot
            size: root.compact ? 5 : 6
            color: root.hue
            pulsing: root.dotPulsing
        }

        Icon {
            visible: root.icon.length > 0
            name: root.icon
            size: root.compact ? 11 : 12
            color: root.subtle ? root.hue : Theme.textSecondary
        }

        Text {
            visible: root.text.length > 0
            text: root.text
            font.family: Theme.fontSans
            font.pixelSize: Theme.textXs
            font.weight: Theme.weightSemiBold
            color: root.subtle ? root.hue : Theme.textSecondary
        }
    }

    AppToolTip {
        visible: root.tooltipText.length > 0 && hoverHandler.hovered
        text: root.tooltipText
    }
}
