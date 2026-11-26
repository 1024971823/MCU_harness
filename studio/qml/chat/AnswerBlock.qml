pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    property string text: ""
    property bool streaming: false
    property string timestamp: ""
    property bool isLastAssistantAnswer: false

    property bool _caretVisible: true
    property bool _copied: false

    implicitHeight: contentCol.implicitHeight

    Timer {
        interval: 530
        repeat: true
        running: root.streaming && root.visible && !AppInfo.screenshotMode
        onTriggered: root._caretVisible = !root._caretVisible
    }

    Timer {
        id: copyTimer
        interval: 1500
        onTriggered: root._copied = false
    }

    HoverHandler {
        id: hoverHandler
    }

    ColumnLayout {
        id: contentCol
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 40
        spacing: Theme.s1 + 2

        MarkdownText {
            id: mdText
            Layout.fillWidth: true
            font.pixelSize: Theme.chatTextSize
            text: root.streaming
                ? (root.text + (root._caretVisible ? " ▍" : ""))
                : root.text
        }

        RowLayout {
            id: actionRow
            Layout.fillWidth: true
            spacing: Theme.s2
            opacity: (!root.streaming && (hoverHandler.hovered || copyBtn.hovered || retryBtn.hovered || root._copied)) ? 1.0 : 0.0
            visible: !root.streaming

            Behavior on opacity {
                NumberAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
            }

            IconButton {
                id: copyBtn
                buttonSize: 24
                iconSize: 13
                iconName: root._copied ? Icons.check : Icons.copy
                iconColor: root._copied ? Theme.success : Theme.textSecondary
                tooltipText: root._copied ? qsTr("Copied") : qsTr("Copy response")
                enabled: actionRow.opacity > 0.1
                onClicked: {
                    Utils.copyToClipboard(root.text)
                    root._copied = true
                    copyTimer.restart()
                }
            }

            IconButton {
                id: retryBtn
                visible: root.isLastAssistantAnswer && !Workbench.turnRunning
                buttonSize: 24
                iconSize: 13
                iconName: Icons.rotateCcw
                tooltipText: qsTr("Retry last prompt")
                enabled: actionRow.opacity > 0.1
                onClicked: Workbench.retryLastTurn()
            }

            Text {
                visible: root.timestamp.length > 0
                text: root.timestamp
                font.family: Theme.fontMono
                font.pixelSize: Theme.textXs
                color: Theme.textMuted
            }

            Item { Layout.fillWidth: true }
        }
    }
}
