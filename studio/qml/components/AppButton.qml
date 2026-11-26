import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio

Button {
    id: root
    property string variant: "secondary" // "primary" | "secondary" | "ghost" | "danger" | "outline"
    property string buttonSize: "md"     // "sm" | "md" | "lg"
    property string iconName: ""
    property bool loading: false
    property string tooltipText: ""
    property string shortcutText: ""

    readonly property int targetHeight: buttonSize === "sm" ? 28 : (buttonSize === "lg" ? 38 : 32)
    readonly property int hPadding: buttonSize === "sm" ? Theme.s2 + 2 : (buttonSize === "lg" ? Theme.s4 : Theme.s3)

    implicitWidth: Math.max(targetHeight, contentRow.implicitWidth + hPadding * 2)
    implicitHeight: targetHeight
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true

    readonly property color bgColor: {
        if (!enabled) return variant === "ghost" ? "transparent" : Theme.bgElevated
        if (variant === "primary") {
            return down ? Theme.accentPressed : (hovered ? Theme.accentHover : Theme.accent)
        }
        if (variant === "danger") {
            return down ? Qt.darker(Theme.danger, 1.15) : (hovered ? Qt.lighter(Theme.danger, 1.08) : Theme.danger)
        }
        if (variant === "ghost") {
            return down ? Theme.bgActive : (hovered ? Theme.bgHover : "transparent")
        }
        if (variant === "outline") {
            return down ? Theme.bgActive : (hovered ? Theme.bgHover : "transparent")
        }
        return down ? Theme.bgActive : (hovered ? Theme.bgHover : Theme.bgElevated)
    }

    readonly property color fgColor: {
        if (!enabled) return Theme.textMuted
        if (variant === "primary" || variant === "danger") return Theme.textOnAccent
        return Theme.textPrimary
    }

    readonly property color bdColor: {
        if (!enabled) return variant === "ghost" ? "transparent" : Theme.borderSubtle
        if (variant === "primary") return Theme.alpha("#FFFFFF", 0.12)
        if (variant === "danger") return Theme.alpha("#FFFFFF", 0.10)
        if (variant === "ghost") return "transparent"
        if (variant === "outline") return hovered ? Theme.borderStrong : Theme.border
        return hovered ? Theme.borderStrong : Theme.border
    }

    background: Rectangle {
        radius: Theme.radiusMd + 2
        color: root.bgColor
        border.color: root.bdColor
        border.width: root.bdColor === "transparent" ? 0 : 1

        Behavior on color {
            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
        }
        Behavior on border.color {
            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
        }

        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            radius: Theme.radiusMd + 4
            color: "transparent"
            border.color: Theme.accent
            border.width: Theme.focusRingWidth
            visible: root.visualFocus
        }
    }

    contentItem: RowLayout {
        id: contentRow
        spacing: Theme.s2

        Spinner {
            visible: root.loading
            size: root.buttonSize === "sm" ? 13 : 14
            color: root.fgColor
        }

        Icon {
            visible: !root.loading && root.iconName.length > 0
            name: root.iconName
            size: root.buttonSize === "sm" ? 13 : 15
            color: root.fgColor
        }

        Text {
            visible: root.text.length > 0
            text: root.text
            font.family: Theme.fontSans
            font.pixelSize: root.buttonSize === "sm" ? Theme.textSm : Theme.textBase
            font.weight: Theme.weightMedium
            color: root.fgColor
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    AppToolTip {
        visible: root.tooltipText.length > 0 && root.hovered && !root.pressed
        text: root.tooltipText
        shortcut: root.shortcutText
    }
}
