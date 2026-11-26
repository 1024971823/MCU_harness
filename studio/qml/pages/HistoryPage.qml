pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    signal sessionOpened()

    property string searchText: ""

    ColumnLayout {
        anchors.fill: parent

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ColumnLayout {
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.topMargin: Theme.s6
                anchors.bottomMargin: Theme.s6
                width: Math.min(parent.width - Theme.s6 * 2, 980)
                spacing: Theme.s4

                // Header
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s3

                    IconChip {
                        icon: Icons.history
                        hue: Theme.hueIndigo
                        size: 38
                        iconSize: 19
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Conversation History")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textXl
                            font.weight: Theme.weightBold
                            color: Theme.textPrimary
                        }

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Resume persisted JSONL engineering sessions under .mcu-harness/sessions/")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textSm
                            color: Theme.textSecondary
                            wrapMode: Text.WordWrap
                        }
                    }

                    AppButton {
                        buttonSize: "sm"
                        variant: "secondary"
                        iconName: Icons.refresh
                        text: qsTr("Refresh")
                        onClicked: Workbench.refreshSessions()
                    }

                    GradientButton {
                        buttonSize: "sm"
                        iconName: Icons.squarePen
                        text: qsTr("New chat")
                        shortcutText: "Ctrl+N"
                        onClicked: {
                            Workbench.newSession()
                            root.sessionOpened()
                        }
                    }
                }

                // Search Bar
                SearchField {
                    Layout.fillWidth: true
                    placeholderText: qsTr("Search sessions by ID or prompt preview...")
                    text: root.searchText
                    onTextEdited: root.searchText = text
                    onCleared: root.searchText = ""
                }

                // Sessions List
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    EmptyState {
                        visible: sessList.count === 0
                        anchors.fill: parent
                        iconName: Icons.history
                        title: qsTr("No Saved Sessions")
                        description: qsTr("Start an Agent conversation to persist messages, thinking traces, tool calls, and hardware telemetry.")
                        actionText: qsTr("Start New Chat")
                        actionIcon: Icons.squarePen
                        onActionTriggered: {
                            Workbench.newSession()
                            root.sessionOpened()
                        }
                    }

                    ListView {
                        id: sessList
                        visible: count > 0
                        anchors.fill: parent
                        spacing: Theme.s2 + 2
                        clip: true
                        model: Workbench.sessionsModel
                        ScrollBar.vertical: AppScrollBar {}

                        delegate: Rectangle {
                            id: sessRow
                            required property string sessionId
                            required property string preview
                            required property int messageCount
                            required property string relativeTime
                            required property string updatedAtText
                            required property bool isCurrent

                            readonly property bool matches: {
                                var q = root.searchText.trim().toLowerCase()
                                if (q.length === 0) return true
                                return sessionId.toLowerCase().indexOf(q) !== -1
                                    || preview.toLowerCase().indexOf(q) !== -1
                            }

                            visible: matches
                            width: sessList.width
                            height: visible ? rowLayout.implicitHeight + Theme.s4 * 2 : 0
                            radius: Theme.radiusLg + 2
                            color: sessRow.isCurrent
                                ? Theme.alpha(Theme.accent, Theme.isDark ? 0.12 : 0.08)
                                : (rowHover.hovered ? Theme.bgHover : Theme.bgSurface)
                            border.color: sessRow.isCurrent
                                ? Theme.alpha(Theme.accent, 0.55)
                                : (rowHover.hovered ? Theme.borderStrong : Theme.borderSubtle)
                            border.width: 1

                            Behavior on color {
                                ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                            }
                            Behavior on border.color {
                                ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                            }

                            HoverHandler {
                                id: rowHover
                                cursorShape: Qt.PointingHandCursor
                            }
                            TapHandler {
                                onTapped: {
                                    if (!sessRow.isCurrent) {
                                        Workbench.loadSession(sessRow.sessionId)
                                    }
                                    root.sessionOpened()
                                }
                            }

                            // Active indicator bar on left
                            Rectangle {
                                visible: sessRow.isCurrent
                                anchors.left: parent.left
                                anchors.top: parent.top
                                anchors.bottom: parent.bottom
                                anchors.margins: Theme.s2
                                width: 3
                                radius: 2
                                gradient: Gradient {
                                    GradientStop { position: 0.0; color: Theme.brandA }
                                    GradientStop { position: 1.0; color: Theme.brandC }
                                }
                            }

                            RowLayout {
                                id: rowLayout
                                anchors.fill: parent
                                anchors.margins: Theme.s4
                                anchors.leftMargin: sessRow.isCurrent ? Theme.s4 + 4 : Theme.s4
                                spacing: Theme.s3 + 2

                                IconChip {
                                    icon: Icons.messageSquare
                                    hue: sessRow.isCurrent ? Theme.accent : Theme.hueIndigo
                                    size: 38
                                    iconSize: 18
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 4

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Theme.s2

                                        Text {
                                            Layout.fillWidth: true
                                            text: sessRow.preview.length > 0 ? sessRow.preview : qsTr("Untitled Session")
                                            font.family: Theme.fontSans
                                            font.pixelSize: Theme.textMd
                                            font.weight: Theme.weightSemiBold
                                            color: Theme.textPrimary
                                            elide: Text.ElideRight
                                        }

                                        Pill {
                                            visible: sessRow.isCurrent
                                            dot: true
                                            hue: Theme.accent
                                            text: qsTr("Active")
                                            compact: true
                                        }
                                    }

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Theme.s2 + 2

                                        Rectangle {
                                            implicitWidth: idTxt.implicitWidth + Theme.s2
                                            implicitHeight: 18
                                            radius: Theme.radiusSm
                                            color: Theme.bgElevated
                                            border.color: Theme.borderSubtle
                                            border.width: 1

                                            Text {
                                                id: idTxt
                                                anchors.centerIn: parent
                                                text: sessRow.sessionId
                                                font.family: Theme.fontMono
                                                font.pixelSize: 10
                                                color: Theme.textMuted
                                            }
                                        }

                                        Text {
                                            text: "·"
                                            color: Theme.textMuted
                                        }

                                        Text {
                                            text: qsTr("%1 messages").arg(sessRow.messageCount)
                                            font.family: Theme.fontSans
                                            font.pixelSize: Theme.textXs
                                            color: Theme.textSecondary
                                        }

                                        Text {
                                            text: "·"
                                            color: Theme.textMuted
                                        }

                                        Text {
                                            text: sessRow.relativeTime + (sessRow.updatedAtText && sessRow.updatedAtText !== "-" ? (" (" + sessRow.updatedAtText + ")") : "")
                                            font.family: Theme.fontSans
                                            font.pixelSize: Theme.textXs
                                            color: Theme.textMuted
                                            elide: Text.ElideRight
                                        }
                                    }
                                }

                                AppButton {
                                    buttonSize: "sm"
                                    variant: sessRow.isCurrent ? "secondary" : "primary"
                                    iconName: Icons.folderOpen
                                    text: sessRow.isCurrent ? qsTr("Resume") : qsTr("Open")
                                    onClicked: {
                                        if (!sessRow.isCurrent) {
                                            Workbench.loadSession(sessRow.sessionId)
                                        }
                                        root.sessionOpened()
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
