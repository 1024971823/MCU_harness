import QtQuick
import QtQuick.Layouts
import McuStudio

RowLayout {
    id: root
    property string label: ""
    property string value: ""
    property string rawValue: value
    property bool monospace: false
    property bool middleElide: false
    property bool copyable: false
    property color valueColor: Theme.textPrimary
    property bool _copied: false

    Layout.fillWidth: true
    spacing: Theme.s3

    Timer {
        id: copyTimer
        interval: 1500
        onTriggered: root._copied = false
    }

    Text {
        Layout.preferredWidth: 112
        text: root.label
        font.family: Theme.fontSans
        font.pixelSize: Theme.textSm
        color: Theme.textSecondary
        elide: Text.ElideRight
    }

    Text {
        Layout.fillWidth: true
        text: root.value.length > 0 ? root.value : "—"
        font.family: root.monospace ? Theme.fontMono : Theme.fontSans
        font.pixelSize: root.monospace ? Theme.textSm : Theme.textBase
        color: root.value.length > 0 ? root.valueColor : Theme.textMuted
        elide: root.middleElide ? Text.ElideMiddle : Text.ElideRight
    }

    IconButton {
        visible: root.copyable && root.rawValue.length > 0
        buttonSize: 24
        iconSize: 13
        iconName: root._copied ? Icons.check : Icons.copy
        iconColor: root._copied ? Theme.success : Theme.textSecondary
        tooltipText: root._copied ? qsTr("Copied") : qsTr("Copy")
        onClicked: {
            Utils.copyToClipboard(root.rawValue)
            root._copied = true
            copyTimer.restart()
        }
    }
}
