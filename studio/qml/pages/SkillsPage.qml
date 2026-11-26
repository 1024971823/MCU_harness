pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    signal runSkillRequested(string cmd)

    property string searchText: ""

    readonly property var quickCommands: [
        { cmd: "/status",   desc: qsTr("Print live probe, target, and telemetry status"), hue: Theme.hueAmber,   icon: Icons.cpu },
        { cmd: "/audit",    desc: qsTr("Summarize recent safety interlock decisions"),      hue: Theme.hueRose,    icon: Icons.shieldCheck },
        { cmd: "/compact",  desc: qsTr("Summarize and compact conversation context"),     hue: Theme.hueViolet,  icon: Icons.layers },
        { cmd: "/clear",    desc: qsTr("Clear current conversation messages"),            hue: Theme.hueSlate,   icon: Icons.trash },
        { cmd: "/sessions", desc: qsTr("List saved engineering sessions"),                hue: Theme.hueIndigo,  icon: Icons.history },
        { cmd: "/help",     desc: qsTr("Show available slash commands & tools"),          hue: Theme.hueCyan,    icon: Icons.info }
    ]

    ScrollView {
        id: scroll
        anchors.fill: parent
        clip: true
        ScrollBar.vertical: AppScrollBar {}
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        Item {
            width: scroll.availableWidth
            implicitHeight: mainCol.implicitHeight + Theme.s6 * 2

            ColumnLayout {
                id: mainCol
                anchors.top: parent.top
                anchors.topMargin: Theme.s6
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(parent.width - Theme.s6 * 2, 1140)
                spacing: Theme.s6

                // Aurora Banner Header
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: bannerCol.implicitHeight + Theme.s5 * 2
                    radius: Theme.radiusXl
                    color: Theme.bgSurface
                    border.color: Theme.border
                    border.width: 1
                    clip: true

                    AuroraBackdrop {
                        anchors.fill: parent
                        animated: false
                        intensity: 0.9
                    }

                    // Top 3px brand gradient hairline
                    Rectangle {
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.leftMargin: Theme.radiusXl
                        anchors.rightMargin: Theme.radiusXl
                        height: 3
                        radius: 1.5
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: Theme.brandA }
                            GradientStop { position: 0.5; color: Theme.brandB }
                            GradientStop { position: 1.0; color: Theme.brandC }
                        }
                    }

                    ColumnLayout {
                        id: bannerCol
                        anchors.fill: parent
                        anchors.margins: Theme.s5
                        spacing: Theme.s4

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s3

                            IconChip {
                                icon: Icons.sparkles
                                hue: Theme.hueViolet
                                size: 44
                                iconSize: 22
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3

                                Text {
                                    Layout.fillWidth: true
                                    text: qsTr("Embedded AI Skills")
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textXl
                                    font.weight: Theme.weightBold
                                    color: Theme.textPrimary
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: qsTr("Structured embedded debugging playbooks and slash commands for fault decoding, peripheral bring-up, and HIL regression")
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textBase
                                    color: Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }
                            }

                            Pill {
                                icon: Icons.sparkles
                                hue: Theme.hueViolet
                                text: qsTr("%1 playbooks").arg(skillRep.count)
                            }
                        }

                        SearchField {
                            Layout.fillWidth: true
                            placeholderText: qsTr("Search skills by command, workflow, or recommended tool...")
                            text: root.searchText
                            onTextEdited: root.searchText = text
                            onCleared: root.searchText = ""
                        }
                    }
                }

                // Playbook Skill Cards Grid (min card width 280)
                GridLayout {
                    id: skillGrid
                    Layout.fillWidth: true
                    columns: Math.max(1, Math.floor((width + Theme.s4) / (280 + Theme.s4)))
                    columnSpacing: Theme.s4
                    rowSpacing: Theme.s4

                    Repeater {
                        id: skillRep
                        model: Workbench.playbooksModel

                        delegate: Rectangle {
                            id: skillCard
                            required property string name
                            required property string command
                            required property string title
                            required property string description
                            required property string usage
                            required property var recommendedTools

                            readonly property var meta: PluginRegistry.skillMeta(command)
                            readonly property color hue: meta.hue
                            readonly property string iconName: meta.icon
                            readonly property bool matches: {
                                var q = root.searchText.trim().toLowerCase()
                                if (q.length === 0) return true
                                if (command.toLowerCase().indexOf(q) !== -1) return true
                                if (title.toLowerCase().indexOf(q) !== -1) return true
                                if (description.toLowerCase().indexOf(q) !== -1) return true
                                var tools = recommendedTools || []
                                for (var i = 0; i < tools.length; ++i) {
                                    if (String(tools[i]).toLowerCase().indexOf(q) !== -1) return true
                                }
                                return false
                            }

                            visible: matches
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            Layout.fillHeight: true
                            implicitHeight: cardCol.implicitHeight + Theme.s4 * 2
                            radius: Theme.radiusXl
                            color: Theme.bgSurface
                            border.color: cardHover.hovered ? skillCard.hue : Theme.borderSubtle
                            border.width: 1

                            Behavior on border.color {
                                ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                            }
                            HoverHandler { id: cardHover }

                            // Subtle top hue accent bar
                            Rectangle {
                                anchors.top: parent.top
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.leftMargin: Theme.radiusXl
                                anchors.rightMargin: Theme.radiusXl
                                height: 3
                                radius: 1.5
                                gradient: Gradient {
                                    orientation: Gradient.Horizontal
                                    GradientStop { position: 0.0; color: Theme.alpha(skillCard.hue, 0.75) }
                                    GradientStop { position: 1.0; color: skillCard.hue }
                                }
                            }

                            ColumnLayout {
                                id: cardCol
                                anchors.fill: parent
                                anchors.margins: Theme.s4
                                anchors.topMargin: Theme.s4 + 2
                                spacing: Theme.s3

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s3

                                    IconChip {
                                        icon: skillCard.iconName
                                        hue: skillCard.hue
                                        size: 40
                                        iconSize: 19
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 4

                                        Rectangle {
                                            implicitWidth: cmdTxt.implicitWidth + Theme.s2 * 2
                                            implicitHeight: 22
                                            radius: Theme.radiusSm + 2
                                            color: Theme.alpha(skillCard.hue, Theme.isDark ? 0.16 : 0.12)
                                            border.color: Theme.alpha(skillCard.hue, 0.35)
                                            border.width: 1

                                            Text {
                                                id: cmdTxt
                                                anchors.centerIn: parent
                                                text: skillCard.command
                                                font.family: Theme.fontMono
                                                font.pixelSize: Theme.textXs
                                                font.weight: Theme.weightSemiBold
                                                color: skillCard.hue
                                            }
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            text: skillCard.title
                                            font.family: Theme.fontSans
                                            font.pixelSize: Theme.textMd
                                            font.weight: Theme.weightSemiBold
                                            color: Theme.textPrimary
                                            wrapMode: Text.WordWrap
                                        }
                                    }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: skillCard.description
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textSm
                                    color: Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }

                                Flow {
                                    visible: (skillCard.recommendedTools || []).length > 0
                                    Layout.fillWidth: true
                                    spacing: Theme.s1 + 2

                                    Repeater {
                                        model: skillCard.recommendedTools || []
                                        delegate: Rectangle {
                                            id: toolTag
                                            required property string modelData
                                            implicitWidth: tagLbl.implicitWidth + Theme.s2 + 4
                                            implicitHeight: 20
                                            radius: Theme.radiusSm
                                            color: Theme.bgElevated
                                            border.color: Theme.borderSubtle
                                            border.width: 1

                                            Text {
                                                id: tagLbl
                                                anchors.centerIn: parent
                                                text: toolTag.modelData
                                                font.family: Theme.fontMono
                                                font.pixelSize: 10
                                                color: Theme.textMuted
                                            }
                                        }
                                    }
                                }

                                Item { Layout.fillHeight: true }

                                Divider { Layout.fillWidth: true }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    Text {
                                        Layout.fillWidth: true
                                        text: skillCard.usage || skillCard.command
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textXs
                                        color: Theme.textMuted
                                        elide: Text.ElideRight
                                    }

                                    GradientButton {
                                        buttonSize: "sm"
                                        iconName: Icons.play
                                        text: qsTr("Run in chat")
                                        colorA: skillCard.hue
                                        colorB: Theme.brandB
                                        colorC: Theme.brandC
                                        onClicked: root.runSkillRequested(skillCard.command)
                                    }
                                }
                            }
                        }
                    }
                }

                // Quick Commands Section
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s3

                    SectionHeader {
                        iconName: Icons.terminal
                        title: qsTr("Quick Commands")
                        subtitle: qsTr("Click any utility slash command to insert it into the chat composer")
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: quickFlow.implicitHeight + Theme.s4 * 2
                        radius: Theme.radiusLg + 2
                        color: Theme.bgSurface
                        border.color: Theme.borderSubtle
                        border.width: 1

                        Flow {
                            id: quickFlow
                            anchors.fill: parent
                            anchors.margins: Theme.s4
                            spacing: Theme.s2 + 2

                            Repeater {
                                model: root.quickCommands

                                delegate: Chip {
                                    id: quickChip
                                    required property var modelData
                                    text: modelData.cmd
                                    icon: modelData.icon
                                    hue: modelData.hue
                                    monospace: true
                                    checkable: false
                                    active: false
                                    tooltipText: modelData.desc
                                    onClicked: root.runSkillRequested(quickChip.modelData.cmd + " ")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
