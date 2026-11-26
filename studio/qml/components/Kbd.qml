import QtQuick
import McuStudio

Rectangle {
    id: root
    property string text: ""

    implicitWidth: label.implicitWidth + Theme.s2 * 1.5
    implicitHeight: 20
    radius: Theme.radiusSm + 1
    color: Theme.codeBg
    border.color: Theme.border
    border.width: 1

    Text {
        id: label
        anchors.centerIn: parent
        text: root.text
        font.family: Theme.fontMono
        font.pixelSize: Theme.textXs
        font.weight: Theme.weightMedium
        color: Theme.textSecondary
    }
}
