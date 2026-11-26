pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    property string text: ""
    property string timestamp: ""
    property bool _copied: false

    implicitHeight: mainCol.implicitHeight

    Timer {
        id: copyResetTimer
        interval: 1500
        onTriggered: root._copied = false
    }

    HoverHandler {
        id: rowHover
    }

    Text {
        id: measureText
        visible: false
        text: root.text
        font.family: Theme.fontSans
        font.pixelSize: Theme.chatTextSize
    }

    ColumnLayout {
        id: mainCol
        width: parent.width
        spacing: Theme.s1

        RowLayout {
            Layout.fillWidth: true
            spacing: 0

            Item { Layout.fillWidth: true }

            Rectangle {
                id: bubble
                readonly property real maxBubbleWidth: Math.max(160, root.width * 0.72)
                Layout.preferredWidth: Math.min(maxBubbleWidth, Math.max(64, measureText.implicitWidth + Theme.s4 * 2))
                Layout.preferredHeight: msgEdit.implicitHeight + Theme.s3 * 2
                topLeftRadius: 16
                topRightRadius: 16
                bottomLeftRadius: 16
                bottomRightRadius: 4
                color: Theme.alpha(Theme.accent, 0.12)
                border.color: Theme.alpha(Theme.accent, 0.35)
                border.width: 1

                TextEdit {
                    id: msgEdit
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Theme.s4
                    anchors.rightMargin: Theme.s4
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    text: root.text
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.chatTextSize
                    color: Theme.textPrimary
                    selectionColor: Theme.accentSubtle
                    selectedTextColor: Theme.textPrimary
                    padding: 0
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            Item { Layout.fillWidth: true }

            IconButton {
                id: copyBtn
                buttonSize: 22
                iconSize: 12
                iconName: root._copied ? Icons.check : Icons.copy
                iconColor: root._copied ? Theme.success : Theme.textMuted
                tooltipText: root._copied ? qsTr("Copied") : qsTr("Copy message")
                opacity: (rowHover.hovered || copyBtn.hovered || root._copied) ? 1.0 : 0.0
                enabled: opacity > 0.1
                onClicked: {
                    Utils.copyToClipboard(root.text)
                    root._copied = true
                    copyResetTimer.restart()
                }

                Behavior on opacity {
                    NumberAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                }
            }

            Text {
                visible: root.timestamp.length > 0
                text: root.timestamp
                font.family: Theme.fontMono
                font.pixelSize: Theme.textXs
                color: Theme.textMuted
                opacity: rowHover.hovered ? 0.95 : 0.55

                Behavior on opacity {
                    NumberAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                }
            }
        }
    }
}
