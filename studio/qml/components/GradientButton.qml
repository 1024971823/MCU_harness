import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio

Button {
    id: root
    property string buttonSize: "md"     // "sm" | "md" | "lg"
    property string iconName: ""
    property bool loading: false
    property bool pill: false
    property color colorA: Theme.brandA
    property color colorB: Theme.brandB
    property color colorC: Theme.brandC
    property string tooltipText: ""
    property string shortcutText: ""

    readonly property int targetHeight: buttonSize === "sm" ? 28 : (buttonSize === "lg" ? 40 : 34)
    readonly property int hPadding: buttonSize === "sm" ? Theme.s3 : (buttonSize === "lg" ? Theme.s5 : Theme.s4)
    readonly property int cornerRadius: pill ? Theme.radiusPill : (Theme.radiusMd + 2)

    implicitWidth: Math.max(targetHeight, contentRow.implicitWidth + hPadding * 2)
    implicitHeight: targetHeight
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true

    background: Rectangle {
        id: bgRect
        radius: root.cornerRadius
        color: root.enabled ? "transparent" : Theme.bgElevated
        border.color: root.enabled ? Theme.alpha("#FFFFFF", 0.14) : Theme.borderSubtle
        border.width: 1

        gradient: root.enabled ? auroraGrad : null

        Gradient {
            id: auroraGrad
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: root.colorA }
            GradientStop { position: 0.5; color: root.colorB }
            GradientStop { position: 1.0; color: root.colorC }
        }

        // Hover / press state sheen
        Rectangle {
            anchors.fill: parent
            radius: bgRect.radius
            color: {
                if (!root.enabled) return "transparent"
                if (root.down) return "#26000000"
                if (root.hovered) return "#1CFFFFFF"
                return "transparent"
            }
            Behavior on color {
                ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
            }
        }

        // Focus ring
        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            radius: bgRect.radius + 2
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
            color: root.enabled ? Theme.textOnAccent : Theme.textMuted
        }

        Icon {
            visible: !root.loading && root.iconName.length > 0
            name: root.iconName
            size: root.buttonSize === "sm" ? 13 : 15
            color: root.enabled ? Theme.textOnAccent : Theme.textMuted
        }

        Text {
            visible: root.text.length > 0
            text: root.text
            font.family: Theme.fontSans
            font.pixelSize: root.buttonSize === "sm" ? Theme.textSm : Theme.textBase
            font.weight: Theme.weightSemiBold
            color: root.enabled ? Theme.textOnAccent : Theme.textMuted
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
