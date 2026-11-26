import QtQuick
import QtQuick.Layouts
import McuStudio

Rectangle {
    id: root
    property string text: ""
    property string variant: "neutral" // "neutral" | "accent" | "success" | "warning" | "danger" | "info" | "purple"
    property string iconName: ""
    property bool compact: false

    readonly property color fgColor: {
        switch (variant) {
        case "accent": return Theme.accent
        case "success": return Theme.success
        case "warning": return Theme.warning
        case "danger": return Theme.danger
        case "info": return Theme.info
        case "purple": return Theme.purple
        default: return Theme.textSecondary
        }
    }

    readonly property color bgColor: {
        switch (variant) {
        case "accent": return Theme.accentSubtle
        case "success": return Theme.successSubtle
        case "warning": return Theme.warningSubtle
        case "danger": return Theme.dangerSubtle
        case "info": return Theme.infoSubtle
        case "purple": return Theme.purpleSubtle
        default: return Theme.bgElevated
        }
    }

    readonly property color borderColor: variant === "neutral"
        ? Theme.border
        : Qt.rgba(fgColor.r, fgColor.g, fgColor.b, 0.35)

    implicitWidth: row.implicitWidth + (compact ? Theme.s2 : Theme.s3)
    implicitHeight: compact ? 20 : 24
    radius: Theme.radiusPill
    color: bgColor
    border.color: borderColor
    border.width: 1

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: Theme.s1

        Icon {
            visible: root.iconName.length > 0
            name: root.iconName
            size: root.compact ? 11 : 12
            color: root.fgColor
        }

        Text {
            text: root.text
            font.family: Theme.fontSans
            font.pixelSize: Theme.textXs
            font.weight: Theme.weightSemiBold
            color: root.fgColor
        }
    }
}
