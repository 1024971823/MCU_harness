import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio

ToolTip {
    id: root
    property string shortcut: ""

    delay: 420
    timeout: 4500
    padding: Theme.s2

    contentItem: RowLayout {
        spacing: Theme.s2

        Text {
            text: root.text
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            font.weight: Theme.weightMedium
            color: Theme.textPrimary
        }

        Kbd {
            visible: root.shortcut.length > 0
            text: root.shortcut
        }
    }

    background: Rectangle {
        color: Theme.bgElevated
        border.color: Theme.borderStrong
        border.width: 1
        radius: Theme.radiusMd + 1
    }
}
