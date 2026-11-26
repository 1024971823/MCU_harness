pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Rectangle {
    id: root
    color: Theme.bgSidebar

    signal openProjectRequested()
    signal launchPlaybook(string commandText)
    signal loadSessionRequested(string sessionId)
    signal navigateToPage(int index)
    signal collapseRequested()

    readonly property var targetObj: Workbench.target || ({})
    readonly property var probeObj: Workbench.probe || ({})
    readonly property string projectName: targetObj.projectDir ? Utils.basename(targetObj.projectDir) : qsTr("No Project")

    Divider {
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        orientation: Qt.Vertical
        width: 1
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Sidebar Header
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.s3
            Layout.rightMargin: Theme.s2
            Layout.topMargin: Theme.s2
            Layout.bottomMargin: Theme.s2
            spacing: Theme.s1

            Icon {
                name: Icons.folder
                size: 14
                color: Theme.accent
            }

            Text {
                Layout.fillWidth: true
                text: root.projectName
                font.family: Theme.fontSans
                font.pixelSize: Theme.textSm
                font.weight: Theme.weightSemiBold
                color: Theme.textPrimary
                elide: Text.ElideMiddle
            }

            IconButton {
                buttonSize: 24
                iconSize: 14
                iconName: Icons.folderOpen
                tooltipText: qsTr("Open Project")
                shortcutText: "Ctrl+O"
                onClicked: root.openProjectRequested()
            }

            IconButton {
                buttonSize: 24
                iconSize: 14
                iconName: Icons.chevronLeft
                tooltipText: qsTr("Hide Sidebar")
                shortcutText: "Ctrl+B"
                onClicked: root.collapseRequested()
            }
        }

        Divider { Layout.fillWidth: true }

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ScrollBar.vertical: AppScrollBar {}
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
                width: root.width - 1
                spacing: Theme.s4
                anchors.margins: Theme.s3

                Item { Layout.preferredHeight: Theme.s1 }

                // Hardware Target Summary
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: Theme.s3
                    Layout.rightMargin: Theme.s3
                    spacing: Theme.s2

                    Text {
                        text: qsTr("TARGET & PROBE")
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textXs
                        font.weight: Theme.weightSemiBold
                        font.letterSpacing: 0.6
                        color: Theme.textMuted
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: hwSummaryCol.implicitHeight + Theme.s3 * 2
                        radius: Theme.radiusMd
                        color: Theme.bgSurface
                        border.color: Theme.borderSubtle
                        border.width: 1

                        ColumnLayout {
                            id: hwSummaryCol
                            anchors.fill: parent
                            anchors.margins: Theme.s3
                            spacing: Theme.s2

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                StatusDot {
                                    color: root.probeObj.connected ? Theme.success : Theme.textMuted
                                    pulsing: !!root.probeObj.connected && root.probeObj.halted === false
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: root.targetObj.device || qsTr("Unknown Target")
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textSm
                                    font.weight: Theme.weightMedium
                                    color: Theme.textPrimary
                                    elide: Text.ElideRight
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                text: {
                                    var parts = []
                                    if (root.targetObj.core) parts.push(root.targetObj.core)
                                    if (root.probeObj.backend) parts.push(root.probeObj.backend)
                                    if (root.probeObj.connected) {
                                        parts.push(root.probeObj.halted ? qsTr("Halted") : qsTr("Running"))
                                    } else {
                                        parts.push(qsTr("Disconnected"))
                                    }
                                    return parts.join(" · ")
                                }
                                font.family: Theme.fontSans
                                font.pixelSize: Theme.textXs
                                color: Theme.textSecondary
                                elide: Text.ElideRight
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s1

                                Badge {
                                    text: "ELF"
                                    variant: root.targetObj.elfExists ? "success" : "neutral"
                                    compact: true
                                }
                                Badge {
                                    text: "SVD"
                                    variant: root.targetObj.svdExists ? "success" : "neutral"
                                    compact: true
                                }
                                Badge {
                                    text: "IOC"
                                    variant: root.targetObj.iocExists ? "info" : "neutral"
                                    compact: true
                                }
                            }
                        }
                    }
                }

                // Embedded Slash Playbooks
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: Theme.s3
                    Layout.rightMargin: Theme.s3
                    spacing: Theme.s1

                    Text {
                        text: qsTr("EMBEDDED PLAYBOOKS")
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textXs
                        font.weight: Theme.weightSemiBold
                        font.letterSpacing: 0.6
                        color: Theme.textMuted
                        Layout.bottomMargin: Theme.s1
                    }

                    Repeater {
                        model: Workbench.playbooksModel

                        delegate: Rectangle {
                            id: pbRow
                            required property string command
                            required property string title
                            required property string description

                            Layout.fillWidth: true
                            implicitHeight: pbCol.implicitHeight + Theme.s2 * 2
                            radius: Theme.radiusMd
                            color: pbHover.hovered ? Theme.bgHover : "transparent"

                            HoverHandler { id: pbHover }
                            TapHandler {
                                onTapped: root.launchPlaybook(pbRow.command + " ")
                            }

                            ColumnLayout {
                                id: pbCol
                                anchors.fill: parent
                                anchors.margins: Theme.s2
                                spacing: 2

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    Text {
                                        text: pbRow.command
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textSm
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.accent
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: pbRow.title
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textSm
                                        color: Theme.textPrimary
                                        elide: Text.ElideRight
                                    }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: pbRow.description
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }

                // Recent Sessions
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: Theme.s3
                    Layout.rightMargin: Theme.s3
                    spacing: Theme.s1

                    RowLayout {
                        Layout.fillWidth: true

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("RECENT SESSIONS")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textXs
                            font.weight: Theme.weightSemiBold
                            font.letterSpacing: 0.6
                            color: Theme.textMuted
                        }

                        AppButton {
                            buttonSize: "sm"
                            variant: "ghost"
                            text: qsTr("All")
                            onClicked: root.navigateToPage(4)
                        }
                    }

                    Repeater {
                        model: Workbench.sessionsModel

                        delegate: Rectangle {
                            id: sessRow
                            required property int index
                            required property string sessionId
                            required property string preview
                            required property bool isCurrent

                            visible: index < 5
                            Layout.fillWidth: true
                            implicitHeight: visible ? sessCol.implicitHeight + Theme.s2 * 2 : 0
                            radius: Theme.radiusMd
                            color: sessRow.isCurrent ? Theme.accentSubtle : (sessHover.hovered ? Theme.bgHover : "transparent")

                            HoverHandler { id: sessHover }
                            TapHandler {
                                onTapped: root.loadSessionRequested(sessRow.sessionId)
                            }

                            ColumnLayout {
                                id: sessCol
                                anchors.fill: parent
                                anchors.margins: Theme.s2
                                spacing: 2

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s1

                                    Icon {
                                        name: Icons.messageSquare
                                        size: 12
                                        color: sessRow.isCurrent ? Theme.accent : Theme.textMuted
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: sessRow.preview.length > 0 ? sessRow.preview : sessRow.sessionId
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textSm
                                        font.weight: sessRow.isCurrent ? Theme.weightMedium : Theme.weightNormal
                                        color: Theme.textPrimary
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }

                Item { Layout.preferredHeight: Theme.s3 }
            }
        }
    }
}
