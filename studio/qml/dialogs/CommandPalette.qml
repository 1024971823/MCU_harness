pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import McuStudio
import "../components"

Popup {
    id: root
    signal executeAction(string actionId, var payload)

    parent: Overlay.overlay
    x: parent ? (parent.width - width) / 2 : 0
    y: 72
    width: Math.min(640, parent ? parent.width - Theme.s8 : 640)
    height: Math.min(460, contentCol.implicitHeight + 2)
    modal: true
    focus: true
    padding: 0
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    property string query: ""
    property int selectedIndex: 0

    readonly property var allCommands: [
        { category: qsTr("Navigate"),   icon: Icons.agent,      label: qsTr("Go to Agent"),              shortcut: "Ctrl+1", action: "nav", payload: 0 },
        { category: qsTr("Navigate"),   icon: Icons.hardware,   label: qsTr("Go to Hardware & Probe"),   shortcut: "Ctrl+2", action: "nav", payload: 1 },
        { category: qsTr("Navigate"),   icon: Icons.watch,      label: qsTr("Go to DWARF Live Watch"),   shortcut: "Ctrl+3", action: "nav", payload: 2 },
        { category: qsTr("Navigate"),   icon: Icons.rtos,       label: qsTr("Go to RTOS Inspector"),     shortcut: "Ctrl+4", action: "nav", payload: 3 },
        { category: qsTr("Navigate"),   icon: Icons.sessions,   label: qsTr("Go to Sessions"),           shortcut: "Ctrl+5", action: "nav", payload: 4 },
        { category: qsTr("Navigate"),   icon: Icons.audit,      label: qsTr("Go to Safety Audit"),       shortcut: "Ctrl+6", action: "nav", payload: 5 },
        { category: qsTr("Navigate"),   icon: Icons.settings,   label: qsTr("Open Settings"),            shortcut: "Ctrl+,", action: "nav", payload: 6 },
        { category: qsTr("Playbook"),   icon: Icons.bug,        label: qsTr("/crash — HardFault Root-Cause"), shortcut: "", action: "playbook", payload: "/crash" },
        { category: qsTr("Playbook"),   icon: Icons.wrench,     label: qsTr("/doctor — 4-Step Peripheral Doctor"), shortcut: "", action: "playbook", payload: "/doctor" },
        { category: qsTr("Playbook"),   icon: Icons.plugZap,    label: qsTr("/bringup — Board Bring-Up SOP"), shortcut: "", action: "playbook", payload: "/bringup" },
        { category: qsTr("Playbook"),   icon: Icons.flask,      label: qsTr("/hil — HIL Verification Suite"), shortcut: "", action: "playbook", payload: "/hil" },
        { category: qsTr("Hardware"),   icon: Icons.plug,       label: qsTr("Connect Debug Probe"),      shortcut: "",       action: "probeConnect", payload: null },
        { category: qsTr("Hardware"),   icon: Icons.unplug,     label: qsTr("Disconnect Debug Probe"),   shortcut: "",       action: "probeDisconnect", payload: null },
        { category: qsTr("Hardware"),   icon: Icons.pause,      label: qsTr("Halt Target Core"),         shortcut: "",       action: "probeHalt", payload: null },
        { category: qsTr("Hardware"),   icon: Icons.play,       label: qsTr("Resume Target Core"),       shortcut: "",       action: "probeResume", payload: null },
        { category: qsTr("Hardware"),   icon: Icons.refresh,    label: qsTr("Refresh Hardware Telemetry"), shortcut: "F5",   action: "refresh", payload: null },
        { category: qsTr("Workspace"),  icon: Icons.messageSquarePlus, label: qsTr("New Agent Session"), shortcut: "Ctrl+N", action: "newSession", payload: null },
        { category: qsTr("Workspace"),  icon: Icons.folderOpen, label: qsTr("Open Project Directory..."),shortcut: "Ctrl+O", action: "openProject", payload: null },
        { category: qsTr("View"),       icon: Icons.panelLeft,  label: qsTr("Toggle Left Sidebar"),      shortcut: "Ctrl+B", action: "toggleSidebar", payload: null },
        { category: qsTr("View"),       icon: Icons.panelBottom,label: qsTr("Toggle Bottom Console"),    shortcut: "Ctrl+J", action: "toggleBottom", payload: null },
        { category: qsTr("View"),       icon: Icons.moon,       label: qsTr("Toggle Light / Dark Theme"),shortcut: "",       action: "theme", payload: null },
        { category: qsTr("View"),       icon: Icons.languages,  label: qsTr("Switch Language (EN / 中文)"),shortcut: "",     action: "language", payload: null },
        { category: qsTr("Account"),    icon: Icons.keyRound,   label: qsTr("Manage License"),           shortcut: "",       action: "license", payload: null },
        { category: qsTr("Account"),    icon: Icons.download,   label: qsTr("Check for Updates"),        shortcut: "",       action: "updates", payload: null },
        { category: qsTr("Help"),       icon: Icons.info,       label: qsTr("About MCU Harness Studio"), shortcut: "",       action: "about", payload: null }
    ]

    readonly property var filteredCommands: {
        var q = query.trim().toLowerCase()
        if (q.length === 0) return allCommands
        var out = []
        for (var i = 0; i < allCommands.length; ++i) {
            var c = allCommands[i]
            var hay = (c.category + " " + c.label).toLowerCase()
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

    Overlay.modal: Rectangle {
        color: Theme.scrim
    }

    Component {
        id: paletteShadowEffect
        MultiEffect {
            shadowEnabled: true
            shadowBlur: 0.85
            shadowVerticalOffset: 12
            shadowColor: "#66000000"
        }
    }

    background: Rectangle {
        radius: Theme.radiusXl
        color: Theme.bgSurface
        border.color: Theme.effectsEnabled ? Theme.border : Theme.borderStrong
        border.width: 1

        layer.enabled: Theme.effectsEnabled
        layer.effect: Theme.effectsEnabled ? paletteShadowEffect : null
    }

    contentItem: ColumnLayout {
        id: contentCol
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: Theme.s3
            spacing: Theme.s2

            AppTextField {
                id: searchBox
                Layout.fillWidth: true
                leadingIcon: Icons.search
                placeholderText: qsTr("Type a command, view, playbook, or probe action...")
                clearable: true
                onTextEdited: {
                    root.query = text
                    root.selectedIndex = 0
                }
                onAccepted: root.runSelected()

                inputField.Keys.onUpPressed: function(event) {
                    event.accepted = true
                    if (root.selectedIndex > 0) root.selectedIndex--
                }
                inputField.Keys.onDownPressed: function(event) {
                    event.accepted = true
                    if (root.selectedIndex < root.filteredCommands.length - 1) root.selectedIndex++
                }
            }
        }

        Divider { Layout.fillWidth: true }

        ListView {
            id: cmdList
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(340, Math.max(80, count * 38))
            clip: true
            model: root.filteredCommands
            currentIndex: root.selectedIndex
            ScrollBar.vertical: AppScrollBar {}

            delegate: Rectangle {
                id: cmdRow
                required property var modelData
                required property int index

                readonly property bool selected: index === root.selectedIndex

                width: cmdList.width
                height: 38
                color: selected ? Theme.accentSubtle : (rowHover.hovered ? Theme.bgHover : "transparent")

                HoverHandler {
                    id: rowHover
                    onHoveredChanged: {
                        if (hovered) root.selectedIndex = cmdRow.index
                    }
                }
                TapHandler {
                    onTapped: {
                        root.selectedIndex = cmdRow.index
                        root.runSelected()
                    }
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s3
                    anchors.rightMargin: Theme.s3
                    spacing: Theme.s3

                    Icon {
                        name: cmdRow.modelData.icon
                        size: 15
                        color: cmdRow.selected ? Theme.accent : Theme.textSecondary
                    }

                    Badge {
                        text: cmdRow.modelData.category
                        variant: "neutral"
                        compact: true
                    }

                    Text {
                        Layout.fillWidth: true
                        text: cmdRow.modelData.label
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textBase
                        font.weight: cmdRow.selected ? Theme.weightMedium : Theme.weightNormal
                        color: Theme.textPrimary
                        elide: Text.ElideRight
                    }

                    Kbd {
                        visible: cmdRow.modelData.shortcut.length > 0
                        text: cmdRow.modelData.shortcut
                    }
                }
            }
        }
    }
}
