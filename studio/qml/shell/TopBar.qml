pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Rectangle {
    id: root
    property int activePage: 0
    property bool threadsVisible: true
    property bool pluginPanelVisible: false

    signal openProjectRequested()
    signal toggleThreadsRequested()
    signal togglePluginPanelRequested()
    signal openCommandPaletteRequested()

    implicitHeight: Theme.topBarHeight
    color: Theme.bgSidebar

    readonly property var targetObj: Workbench.target || ({})
    readonly property string projectName: targetObj.projectDir ? Utils.basename(targetObj.projectDir) : qsTr("No Project")
    readonly property string deviceName: targetObj.device || ""

    // Hidden proxy to look up the active session preview for the current Workbench.sessionId
    Item {
        visible: false
        Repeater {
            id: sessionLookup
            model: Workbench.sessionsModel
            delegate: Item {
                required property string sessionId
                required property string preview
            }
        }
    }

    readonly property string activeThreadTitle: {
        if (root.activePage === 0 && Workbench.messagesModel && Workbench.messagesModel.count === 0) {
            return qsTr("New chat")
        }
        var sid = Workbench.sessionId || ""
        for (var i = 0; i < sessionLookup.count; ++i) {
            var it = sessionLookup.itemAt(i)
            if (it && it["sessionId"] === sid) {
                var prev = it["preview"]
                return prev && prev.length > 0 ? prev : sid
            }
        }
        return sid.length > 0 ? sid : qsTr("New conversation")
    }

    readonly property string engineLabel: {
        if (Workbench.engineStatus === "ready") return qsTr("Ready")
        if (Workbench.engineStatus === "error") return qsTr("Error")
        return qsTr("Starting")
    }

    readonly property color engineHue: {
        if (Workbench.engineStatus === "ready") return Theme.hueEmerald
        if (Workbench.engineStatus === "error") return Theme.hueRose
        return Theme.hueAmber
    }

    Divider {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.s3
        anchors.rightMargin: Theme.s3
        spacing: Theme.s2

        // Left: Threads toggle (Chat page) + Project pill
        IconButton {
            visible: root.activePage === 0
            buttonSize: 30
            iconSize: 16
            iconName: root.threadsVisible ? Icons.panelLeftClose : Icons.panelLeftOpen
            active: root.threadsVisible
            tooltipText: root.threadsVisible ? qsTr("Hide Threads") : qsTr("Show Threads")
            shortcutText: "Ctrl+B"
            onClicked: root.toggleThreadsRequested()
        }

        Rectangle {
            id: projectPill
            Layout.preferredHeight: 30
            implicitWidth: Math.min(340, projectRow.implicitWidth + Theme.s3 * 2)
            radius: Theme.radiusPill
            color: projHover.hovered ? Theme.bgHover : Theme.bgElevated
            border.color: projHover.hovered ? Theme.borderStrong : Theme.border
            border.width: 1

            Behavior on color {
                ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
            }

            HoverHandler {
                id: projHover
                cursorShape: Qt.PointingHandCursor
            }
            TapHandler {
                onTapped: root.openProjectRequested()
            }

            RowLayout {
                id: projectRow
                anchors.fill: parent
                anchors.leftMargin: Theme.s3
                anchors.rightMargin: Theme.s2 + 2
                spacing: Theme.s2

                Icon {
                    name: Icons.folder
                    size: 14
                    color: Theme.accent
                }

                Text {
                    Layout.maximumWidth: 160
                    text: root.projectName
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textSm
                    font.weight: Theme.weightSemiBold
                    color: Theme.textPrimary
                    elide: Text.ElideMiddle
                }

                Pill {
                    visible: root.deviceName.length > 0
                    text: root.deviceName
                    hue: Theme.hueAmber
                    compact: true
                }

                Icon {
                    name: Icons.chevronDown
                    size: 12
                    color: Theme.textMuted
                }
            }

            AppToolTip {
                visible: projHover.hovered
                text: root.targetObj.projectDir ? root.targetObj.projectDir : qsTr("Open Embedded Project Directory")
                shortcut: "Ctrl+O"
            }
        }

        // Centre: Current thread title
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Text {
                anchors.centerIn: parent
                width: Math.min(implicitWidth, parent.width - Theme.s4)
                text: root.activeThreadTitle
                font.family: Theme.fontSans
                font.pixelSize: Theme.textBase
                font.weight: Theme.weightSemiBold
                color: Theme.textSecondary
                horizontalAlignment: Text.AlignHCenter
                elide: Text.ElideRight
            }
        }

        // Right: Engine status chip + Command Palette pill + Language toggle + Plugin panel toggle
        Pill {
            text: root.engineLabel
            hue: root.engineHue
            dot: true
            dotPulsing: Workbench.turnRunning || Workbench.engineStatus === "starting"
            tooltipText: Workbench.engineError && Workbench.engineError.length > 0
                ? Workbench.engineError
                : qsTr("Agent engine status: %1").arg(root.engineLabel)
        }

        Rectangle {
            id: cmdPill
            Layout.preferredHeight: 30
            implicitWidth: cmdRow.implicitWidth + Theme.s2 * 2 + 2
            radius: Theme.radiusPill
            color: cmdHover.hovered ? Theme.bgHover : Theme.bgElevated
            border.color: cmdHover.hovered ? Theme.borderStrong : Theme.border
            border.width: 1

            Behavior on color {
                ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
            }

            HoverHandler {
                id: cmdHover
                cursorShape: Qt.PointingHandCursor
            }
            TapHandler {
                onTapped: root.openCommandPaletteRequested()
            }

            RowLayout {
                id: cmdRow
                anchors.centerIn: parent
                spacing: Theme.s2

                Icon {
                    name: Icons.search
                    size: 13
                    color: Theme.textMuted
                }

                Text {
                    text: qsTr("Search")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textSm
                    color: Theme.textMuted
                }

                Kbd {
                    text: "Ctrl+K"
                }
            }

            AppToolTip {
                visible: cmdHover.hovered
                text: qsTr("Open Command Palette")
                shortcut: "Ctrl+K"
            }
        }

        IconButton {
            buttonSize: 30
            iconSize: 15
            iconName: Icons.languages
            tooltipText: Settings.language === "zh_CN" ? qsTr("Switch to English") : qsTr("Switch to 中文")
            onClicked: Settings.setLanguage(Settings.language === "zh_CN" ? "en" : "zh_CN")
        }

        IconButton {
            buttonSize: 30
            iconSize: 16
            iconName: root.pluginPanelVisible ? Icons.panelRightClose : Icons.panelRightOpen
            active: root.pluginPanelVisible
            tooltipText: root.pluginPanelVisible ? qsTr("Hide Plugin Panel") : qsTr("Show Plugin Panel")
            shortcutText: "Ctrl+J"
            onClicked: root.togglePluginPanelRequested()
        }
    }
}
