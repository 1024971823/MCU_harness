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
        anchors.margins: Theme.s6
        spacing: Theme.s4

        SectionHeader {
            iconName: Icons.sessions
            title: qsTr("Agent Sessions")
            subtitle: qsTr("Resume persisted JSONL engineering conversations stored under .mcu-harness/sessions/")

            AppButton {
                buttonSize: "sm"
                variant: "secondary"
                iconName: Icons.refresh
                text: qsTr("Refresh")
                onClicked: Workbench.refreshSessions()
            }

            AppButton {
                buttonSize: "sm"
                variant: "primary"
                iconName: Icons.messageSquarePlus
                text: qsTr("New Session")
                shortcutText: "Ctrl+N"
                onClicked: {
                    Workbench.newSession()
                    root.sessionOpened()
                }
            }
        }

        AppTextField {
            Layout.fillWidth: true
            leadingIcon: Icons.search
            placeholderText: qsTr("Filter sessions by ID or prompt preview...")
            clearable: true
            text: root.searchText
            onTextEdited: root.searchText = text
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            EmptyState {
                visible: sessList.count === 0
                anchors.fill: parent
                iconName: Icons.sessions
                title: qsTr("No Saved Sessions")
                description: qsTr("Start an Agent conversation to persist messages, tool calls, and hardware telemetry events.")
                actionText: qsTr("Start New Session")
                actionIcon: Icons.plus
                onActionTriggered: {
                    Workbench.newSession()
                    root.sessionOpened()
                }
            }

            ListView {
                id: sessList
                visible: count > 0
                anchors.fill: parent
                spacing: Theme.s3
                clip: true
                model: Workbench.sessionsModel
                ScrollBar.vertical: AppScrollBar {}

                delegate: Rectangle {
                    id: sessCard
                    required property string sessionId
                    required property string preview
                    required property int messageCount
                    required property string relativeTime
                    required property bool isCurrent

                    readonly property bool matches: {
                        var q = root.searchText.trim().toLowerCase()
                        if (q.length === 0) return true
                        return sessionId.toLowerCase().indexOf(q) !== -1
                            || preview.toLowerCase().indexOf(q) !== -1
                    }

                    visible: matches
                    width: sessList.width
                    height: visible ? cardRow.implicitHeight + Theme.s4 * 2 : 0
                    radius: Theme.radiusLg
                    color: sessCard.isCurrent ? Theme.bgElevated : Theme.bgSurface
                    border.color: sessCard.isCurrent ? Theme.accent : (cardHover.hovered ? Theme.borderStrong : Theme.borderSubtle)
                    border.width: 1

                    Behavior on border.color { ColorAnimation { duration: Theme.animFast } }
                    HoverHandler { id: cardHover }

                    RowLayout {
                        id: cardRow
                        anchors.fill: parent
                        anchors.margins: Theme.s4
                        spacing: Theme.s4

                        Rectangle {
                            Layout.preferredWidth: 36
                            Layout.preferredHeight: 36
                            radius: Theme.radiusMd
                            color: sessCard.isCurrent ? Theme.accentSubtle : Theme.bgElevated

                            Icon {
                                anchors.centerIn: parent
                                name: Icons.messageSquare
                                size: 17
                                color: sessCard.isCurrent ? Theme.accent : Theme.textSecondary
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 4

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                Text {
                                    Layout.fillWidth: true
                                    text: sessCard.preview.length > 0 ? sessCard.preview : qsTr("Untitled Session")
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textMd
                                    font.weight: Theme.weightSemiBold
                                    color: Theme.textPrimary
                                    elide: Text.ElideRight
                                }

                                Badge {
                                    visible: sessCard.isCurrent
                                    text: qsTr("Current")
                                    variant: "accent"
                                    compact: true
                                }
                            }

                            RowLayout {
                                spacing: Theme.s3

                                Text {
                                    text: sessCard.sessionId
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textMuted
                                }

                                Text {
                                    text: "·"
                                    color: Theme.textMuted
                                }

                                Text {
                                    text: qsTr("%1 messages").arg(sessCard.messageCount)
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textSecondary
                                }

                                Text {
                                    text: "·"
                                    color: Theme.textMuted
                                }

                                Text {
                                    text: sessCard.relativeTime
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textSecondary
                                }
                            }
                        }

                        AppButton {
                            buttonSize: "sm"
                            variant: sessCard.isCurrent ? "secondary" : "primary"
                            iconName: Icons.folderOpen
                            text: sessCard.isCurrent ? qsTr("View") : qsTr("Open")
                            onClicked: {
                                if (!sessCard.isCurrent) {
                                    Workbench.loadSession(sessCard.sessionId)
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
