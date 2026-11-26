pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    property int rowIndex: -1
    property string text: ""
    property bool streaming: false
    property bool expanded: false
    property real durationMs: 0
    property real startedMs: 0

    property real _nowMs: Date.now()
    readonly property bool allowExpand: Settings.showThinking
    readonly property bool showingFullBody: allowExpand && expanded && text.length > 0
    readonly property bool showingTailBody: allowExpand && streaming && !expanded && text.length > 0
    readonly property bool bodyVisible: showingFullBody || showingTailBody

    readonly property string elapsedLabel: {
        var ms = streaming
            ? Math.max(0, (startedMs > 0 ? (_nowMs - startedMs) : 0))
            : Math.max(0, durationMs)
        return (ms / 1000.0).toFixed(1) + "s"
    }

    readonly property string tailText: {
        if (text.length <= 220) return text
        return "…" + text.slice(text.length - 220)
    }

    Component.onCompleted: _nowMs = Date.now()
    onStartedMsChanged: _nowMs = Date.now()

    implicitHeight: blockRow.implicitHeight

    Timer {
        interval: 100
        repeat: true
        running: root.streaming && root.visible && !AppInfo.screenshotMode
        onTriggered: root._nowMs = Date.now()
    }

    Item {
        id: blockRow
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 40
        implicitHeight: contentCol.implicitHeight

        // 2px vertical brand gradient rule along the left edge
        Rectangle {
            id: gradientRule
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: 2
            anchors.bottomMargin: 2
            width: 2
            radius: 1
            opacity: root.streaming ? 0.95 : (root.bodyVisible ? 0.72 : 0.42)

            gradient: Gradient {
                GradientStop { position: 0.0; color: Theme.brandA }
                GradientStop { position: 1.0; color: Theme.brandC }
            }
        }

        ColumnLayout {
            id: contentCol
            anchors.left: gradientRule.right
            anchors.right: parent.right
            anchors.leftMargin: Theme.s3
            spacing: Theme.s1 + 2

            // Clickable header row
            Item {
                id: headerItem
                Layout.fillWidth: true
                implicitHeight: headerRow.implicitHeight + Theme.s1

                HoverHandler {
                    id: headerHover
                    enabled: root.allowExpand
                    cursorShape: root.allowExpand ? Qt.PointingHandCursor : Qt.ArrowCursor
                }

                TapHandler {
                    enabled: root.allowExpand
                    onTapped: {
                        if (root.rowIndex >= 0) {
                            Workbench.messagesModel.setExpanded(root.rowIndex, !root.expanded)
                        }
                    }
                }

                RowLayout {
                    id: headerRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Theme.s2

                    Icon {
                        name: Icons.brain
                        size: 14
                        color: root.streaming
                            ? Theme.hueViolet
                            : (headerHover.hovered || root.expanded ? Theme.hueViolet : Theme.textMuted)
                    }

                    Text {
                        text: root.streaming
                            ? qsTr("Thinking")
                            : qsTr("Thought for %1").arg(root.elapsedLabel)
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textBase
                        font.weight: Theme.weightMedium
                        color: root.streaming
                            ? Theme.textPrimary
                            : (headerHover.hovered ? Theme.textPrimary : Theme.textSecondary)
                    }

                    TypingDots {
                        visible: root.streaming
                        active: root.streaming && root.visible
                        color: Theme.hueViolet
                        dotSize: 4
                    }

                    Text {
                        visible: root.streaming
                        text: root.elapsedLabel
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.textSm
                        color: Theme.hueViolet
                    }

                    Icon {
                        visible: !root.streaming && root.allowExpand
                        name: root.expanded ? Icons.chevronDown : Icons.chevronRight
                        size: 13
                        color: headerHover.hovered ? Theme.textPrimary : Theme.textMuted
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            // Single Text element for reasoning body (no per-delta child churn)
            Text {
                id: bodyText
                visible: root.bodyVisible
                Layout.fillWidth: true
                text: root.showingFullBody ? root.text : root.tailText
                wrapMode: Text.Wrap
                font.family: Theme.fontSans
                font.pixelSize: Theme.textBase
                font.italic: root.showingTailBody
                lineHeight: 1.55
                color: root.showingFullBody ? Theme.textSecondary : Theme.textMuted
            }
        }
    }
}
