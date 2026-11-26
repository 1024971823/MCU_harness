pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

AppDialog {
    id: root
    signal executeAction(string actionId, var payload)

    dialogWidth: 640
    iconName: Icons.command
    iconColor: Theme.accent
    title: qsTr("Command Palette")
    subtitle: qsTr("Jump to pages, open plugins, launch skills, or control the debug probe")

    property string query: ""
    property int selectedIndex: 0

    readonly property var allCommands: {
        var out = []
        var plugs = PluginRegistry.plugins
        for (var i = 0; i < plugs.length; ++i) {
            var p = plugs[i]
            out.push({
                category: qsTr("Plugins"),
                icon: p.icon,
                hue: p.hue,
                label: qsTr("Open plugin: %1").arg(p.name),
                shortcut: "",
                action: "openPlugin",
                payload: p.id
            })
        }

        var skills = [
            { cmd: "/crash",    label: qsTr("/crash — HardFault Root-Cause") },
            { cmd: "/doctor",   label: qsTr("/doctor — 4-Step Peripheral Doctor") },
            { cmd: "/bringup",  label: qsTr("/bringup — Board Bring-Up SOP") },
            { cmd: "/refactor", label: qsTr("/refactor — Embedded Safe Refactor") },
            { cmd: "/hil",      label: qsTr("/hil — HIL Verification Suite") }
        ]
        for (var s = 0; s < skills.length; ++s) {
            var meta = PluginRegistry.skillMeta(skills[s].cmd)
            out.push({
                category: qsTr("Skills"),
                icon: meta.icon,
                hue: meta.hue,
                label: skills[s].label,
                shortcut: "",
                action: "playbook",
                payload: skills[s].cmd
            })
        }

        var pages = [
            { icon: Icons.messageSquare, hue: Theme.accent,    label: qsTr("Go to Chat"),     shortcut: "Ctrl+1", payload: 0 },
            { icon: Icons.sparkles,      hue: Theme.hueIndigo, label: qsTr("Go to Skills"),   shortcut: "Ctrl+2", payload: 1 },
            { icon: Icons.puzzle,        hue: Theme.hueViolet, label: qsTr("Go to Plugins"),  shortcut: "Ctrl+3", payload: 2 },
            { icon: Icons.history,       hue: Theme.hueSky,    label: qsTr("Go to History"),  shortcut: "Ctrl+4", payload: 3 },
            { icon: Icons.settings,      hue: Theme.hueSlate,  label: qsTr("Open Settings"),  shortcut: "Ctrl+,", payload: 4 }
        ]
        for (var n = 0; n < pages.length; ++n) {
            out.push({
                category: qsTr("Pages"),
                icon: pages[n].icon,
                hue: pages[n].hue,
                label: pages[n].label,
                shortcut: pages[n].shortcut,
                action: "nav",
                payload: pages[n].payload
            })
        }

        out.push({ category: qsTr("Hardware"),  icon: Icons.plug,       hue: Theme.hueAmber,   label: qsTr("Connect Debug Probe"),        shortcut: "",       action: "probeConnect",    payload: null })
        out.push({ category: qsTr("Hardware"),  icon: Icons.unplug,     hue: Theme.hueSlate,   label: qsTr("Disconnect Debug Probe"),     shortcut: "",       action: "probeDisconnect", payload: null })
        out.push({ category: qsTr("Hardware"),  icon: Icons.pause,      hue: Theme.hueAmber,   label: qsTr("Halt Target Core"),           shortcut: "",       action: "probeHalt",       payload: null })
        out.push({ category: qsTr("Hardware"),  icon: Icons.play,       hue: Theme.hueEmerald, label: qsTr("Resume Target Core"),         shortcut: "",       action: "probeResume",     payload: null })
        out.push({ category: qsTr("Hardware"),  icon: Icons.refresh,    hue: Theme.hueCyan,    label: qsTr("Refresh Hardware Telemetry"), shortcut: "F5",     action: "refresh",         payload: null })

        out.push({ category: qsTr("Workspace"), icon: Icons.squarePen,  hue: Theme.brandB,     label: qsTr("New Chat"),                   shortcut: "Ctrl+N", action: "newSession",        payload: null })
        out.push({ category: qsTr("Workspace"), icon: Icons.folderOpen, hue: Theme.hueAmber,   label: qsTr("Open Project Directory..."),  shortcut: "Ctrl+O", action: "openProject",       payload: null })
        out.push({ category: qsTr("View"),      icon: Icons.panelLeft,  hue: Theme.hueSky,     label: qsTr("Toggle Threads Sidebar"),     shortcut: "Ctrl+B", action: "toggleThreads",     payload: null })
        out.push({ category: qsTr("View"),      icon: Icons.panelRight, hue: Theme.hueCyan,    label: qsTr("Toggle Plugin Panel"),        shortcut: "Ctrl+J", action: "togglePluginPanel", payload: null })
        out.push({ category: qsTr("View"),      icon: Theme.isDark ? Icons.sun : Icons.moon, hue: Theme.hueViolet, label: qsTr("Toggle Light / Dark Theme"), shortcut: "", action: "theme", payload: null })
        out.push({ category: qsTr("View"),      icon: Icons.languages,  hue: Theme.hueEmerald, label: qsTr("Switch Language (EN / 中文)"), shortcut: "",       action: "language",          payload: null })

        out.push({ category: qsTr("Account"),   icon: Icons.keyRound,   hue: Theme.hueEmerald, label: qsTr("Manage License"),             shortcut: "",       action: "license",           payload: null })
        out.push({ category: qsTr("Account"),   icon: Icons.download,   hue: Theme.hueSky,     label: qsTr("Check for Updates"),          shortcut: "",       action: "updates",           payload: null })
        out.push({ category: qsTr("Help"),      icon: Icons.info,       hue: Theme.hueSlate,   label: qsTr("About MCU Harness Studio"),   shortcut: "",       action: "about",             payload: null })
        return out
    }

    readonly property var filteredCommands: {
        var q = query.trim().toLowerCase()
        if (q.length === 0) return allCommands
        var out = []
        for (var i = 0; i < allCommands.length; ++i) {
            var c = allCommands[i]
            var hay = (c.category + " " + c.label + " " + (c.payload ? String(c.payload) : "")).toLowerCase()
            if (hay.indexOf(q) !== -1) out.push(c)
        }
        return out
    }

    function openPalette() {
        query = ""
        searchBox.text = ""
        selectedIndex = 0
        open()
        searchBox.inputField.forceActiveFocus()
    }

    function runSelected() {
        if (filteredCommands.length === 0) return
        var idx = Math.max(0, Math.min(selectedIndex, filteredCommands.length - 1))
        var cmd = filteredCommands[idx]
        close()
        executeAction(cmd.action, cmd.payload)
    }

    SearchField {
        id: searchBox
        Layout.fillWidth: true
        fieldHeight: 36
        placeholderText: qsTr("Type a plugin, skill, page, or hardware action...")
        onTextEdited: {
            root.query = text
            root.selectedIndex = 0
        }
        onCleared: {
            root.query = ""
            root.selectedIndex = 0
        }
        onAccepted: root.runSelected()

        inputField.Keys.onUpPressed: function(event) {
            event.accepted = true
            if (root.selectedIndex > 0) {
                root.selectedIndex--
                cmdList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
            }
        }
        inputField.Keys.onDownPressed: function(event) {
            event.accepted = true
            if (root.selectedIndex < root.filteredCommands.length - 1) {
                root.selectedIndex++
                cmdList.positionViewAtIndex(root.selectedIndex, ListView.Contain)
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 340
        radius: Theme.radiusLg
        color: Theme.bgElevated
        border.color: Theme.borderSubtle
        border.width: 1
        clip: true

        ListView {
            id: cmdList
            anchors.fill: parent
            anchors.margins: Theme.s1
            clip: true
            spacing: 2
            model: root.filteredCommands
            currentIndex: root.selectedIndex
            ScrollBar.vertical: AppScrollBar {}

            delegate: Column {
                id: entryWrap
                required property var modelData
                required property int index

                readonly property bool showGroupHeader: index === 0 || root.filteredCommands[index - 1].category !== modelData.category
                readonly property bool selected: index === root.selectedIndex

                width: cmdList.width

                Item {
                    visible: entryWrap.showGroupHeader
                    width: parent.width
                    height: 24

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.s2 + 2
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 3
                        text: entryWrap.modelData.category
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textXs
                        font.weight: Theme.weightSemiBold
                        font.letterSpacing: 0.4
                        color: Theme.textMuted
                    }
                }

                Rectangle {
                    width: parent.width
                    height: 38
                    radius: Theme.radiusMd + 1
                    color: entryWrap.selected
                        ? Theme.alpha(entryWrap.modelData.hue, Theme.isDark ? 0.16 : 0.12)
                        : (rowHover.hovered ? Theme.bgHover : "transparent")
                    border.color: entryWrap.selected ? Theme.alpha(entryWrap.modelData.hue, 0.35) : "transparent"
                    border.width: 1

                    HoverHandler {
                        id: rowHover
                        cursorShape: Qt.PointingHandCursor
                        onHoveredChanged: {
                            if (hovered) root.selectedIndex = entryWrap.index
                        }
                    }
                    TapHandler {
                        onTapped: {
                            root.selectedIndex = entryWrap.index
                            root.runSelected()
                        }
                    }

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.s2 + 2
                        anchors.rightMargin: Theme.s2 + 2
                        spacing: Theme.s2 + 2

                        IconChip {
                            icon: entryWrap.modelData.icon
                            hue: entryWrap.modelData.hue
                            size: 26
                            iconSize: 14
                        }

                        Text {
                            Layout.fillWidth: true
                            text: entryWrap.modelData.label
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textBase
                            font.weight: entryWrap.selected ? Theme.weightSemiBold : Theme.weightNormal
                            color: Theme.textPrimary
                            elide: Text.ElideRight
                        }

                        Kbd {
                            visible: (entryWrap.modelData.shortcut || "").length > 0
                            text: entryWrap.modelData.shortcut || ""
                        }
                    }
                }
            }
        }

        Text {
            visible: cmdList.count === 0
            anchors.centerIn: parent
            text: qsTr("No matching commands")
            font.family: Theme.fontSans
            font.pixelSize: Theme.textBase
            color: Theme.textMuted
        }
    }
}
