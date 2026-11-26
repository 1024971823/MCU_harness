pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Popup {
    id: root
    property string filterText: ""

    signal skillSelected(string cmd)

    padding: Theme.s1 + 2
    modal: false
    focus: false
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent

    readonly property var builtinMetaCommands: [
        {
            command: "/status",
            title: qsTr("Target & Probe Status"),
            description: qsTr("Show hardware, probe, ELF/SVD, and log status"),
            category: "meta"
        },
        {
            command: "/audit",
            title: qsTr("Safety Audit Trail"),
            description: qsTr("Inspect recent HITL safety interlock audit trail"),
            category: "meta"
        },
        {
            command: "/compact",
            title: qsTr("Compact Context"),
            description: qsTr("Summarize older messages to free context window"),
            category: "meta"
        },
        {
            command: "/clear",
            title: qsTr("Clear Conversation"),
            description: qsTr("Clear the current conversation timeline"),
            category: "meta"
        },
        {
            command: "/sessions",
            title: qsTr("Saved Sessions"),
            description: qsTr("List and switch saved debugging sessions"),
            category: "meta"
        },
        {
            command: "/help",
            title: qsTr("Command Help"),
            description: qsTr("Show available commands and shortcuts"),
            category: "meta"
        }
    ]

    property var filteredItems: []
    property int _playbookRev: 0

    function rebuildFiltered() {
        var q = String(filterText || "").trim().toLowerCase()
        var out = []
        for (var i = 0; i < playbookRepeater.count; ++i) {
            var item = playbookRepeater.itemAt(i)
            if (!item) continue
            var cmd = item["command"] || ""
            var title = item["title"] || ""
            var desc = item["description"] || ""
            if (q.length === 0 || cmd.toLowerCase().indexOf(q) === 0 || title.toLowerCase().indexOf(q) >= 0) {
                out.push({
                    command: cmd,
                    title: title,
                    description: desc,
                    category: "playbook"
                })
            }
        }
        for (var j = 0; j < builtinMetaCommands.length; ++j) {
            var m = builtinMetaCommands[j]
            if (q.length === 0 || m.command.toLowerCase().indexOf(q) === 0 || m.title.toLowerCase().indexOf(q) >= 0) {
                out.push(m)
            }
        }
        filteredItems = out
        if (out.length > 0) {
            listView.currentIndex = 0
        } else {
            listView.currentIndex = -1
        }
    }

    function moveUp() {
        if (listView.count === 0) return
        listView.currentIndex = (listView.currentIndex - 1 + listView.count) % listView.count
        listView.positionViewAtIndex(listView.currentIndex, ListView.Contain)
    }

    function moveDown() {
        if (listView.count === 0) return
        listView.currentIndex = (listView.currentIndex + 1) % listView.count
        listView.positionViewAtIndex(listView.currentIndex, ListView.Contain)
    }

    function acceptCurrent() {
        if (!opened || listView.count === 0) return false
        var idx = listView.currentIndex >= 0 ? listView.currentIndex : 0
        if (idx >= 0 && idx < filteredItems.length) {
            var chosen = filteredItems[idx]
            root.close()
            root.skillSelected(chosen.command)
            return true
        }
        return false
    }

    onFilterTextChanged: rebuildFiltered()
    onOpenedChanged: {
        if (opened) {
            rebuildFiltered()
        }
    }
    Component.onCompleted: rebuildFiltered()

    // Hidden repeater to observe Workbench.playbooksModel with localized roles
    Item {
        visible: false
        Repeater {
            id: playbookRepeater
            model: Workbench.playbooksModel
            delegate: Item {
                required property string command
                required property string title
                required property string description
                onTitleChanged: root.rebuildFiltered()
                onDescriptionChanged: root.rebuildFiltered()
            }
            onCountChanged: root.rebuildFiltered()
        }
    }

    background: Rectangle {
        radius: Theme.radiusXl
        color: Theme.bgSurface
        border.color: Theme.borderStrong
        border.width: 1
    }

    contentItem: ColumnLayout {
        spacing: Theme.s1

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.s2
            Layout.rightMargin: Theme.s2
            Layout.topMargin: Theme.s1
            spacing: Theme.s2

            Icon {
                name: Icons.sparkles
                size: 13
                color: Theme.accent
            }

            Text {
                Layout.fillWidth: true
                text: qsTr("Skills & Commands")
                font.family: Theme.fontSans
                font.pixelSize: Theme.textXs
                font.weight: Theme.weightSemiBold
                color: Theme.textMuted
            }

            Text {
                text: qsTr("↑↓ Select · Enter")
                font.family: Theme.fontMono
                font.pixelSize: 10
                color: Theme.textMuted
            }
        }

        ListView {
            id: listView
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(280, Math.max(36, contentHeight))
            clip: true
            spacing: 2
            model: root.filteredItems
            ScrollBar.vertical: AppScrollBar {}

            delegate: Rectangle {
                id: rowItem
                required property int index
                required property var modelData

                readonly property var meta: PluginRegistry.skillMeta(modelData.command)
                readonly property color hue: meta ? meta.hue : Theme.hueIndigo
                readonly property string iconName: meta ? meta.icon : Icons.sparkles
                readonly property bool isCurrent: listView.currentIndex === index

                width: listView.width
                height: rowLayout.implicitHeight + Theme.s1 * 2 + 2
                radius: Theme.radiusMd
                color: isCurrent
                    ? Theme.alpha(rowItem.hue, Theme.isDark ? 0.16 : 0.11)
                    : (rowHover.hovered ? Theme.bgHover : "transparent")
                border.color: isCurrent ? Theme.alpha(rowItem.hue, 0.35) : "transparent"
                border.width: 1

                HoverHandler {
                    id: rowHover
                    cursorShape: Qt.PointingHandCursor
                    onHoveredChanged: {
                        if (hovered) {
                            listView.currentIndex = rowItem.index
                        }
                    }
                }

                TapHandler {
                    onTapped: {
                        root.close()
                        root.skillSelected(rowItem.modelData.command)
                    }
                }

                RowLayout {
                    id: rowLayout
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Theme.s2
                    anchors.rightMargin: Theme.s2
                    spacing: Theme.s2

                    IconChip {
                        size: 24
                        icon: rowItem.iconName
                        hue: rowItem.hue
                    }

                    Text {
                        text: rowItem.modelData.command
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.textSm
                        font.weight: Theme.weightSemiBold
                        color: rowItem.hue
                    }

                    Text {
                        text: rowItem.modelData.title
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textSm
                        font.weight: Theme.weightMedium
                        color: Theme.textPrimary
                    }

                    Text {
                        Layout.fillWidth: true
                        text: rowItem.modelData.description
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textXs
                        color: Theme.textMuted
                        elide: Text.ElideRight
                    }
                }
            }
        }

        Text {
            visible: listView.count === 0
            Layout.fillWidth: true
            Layout.margins: Theme.s3
            text: qsTr("No matching skills or commands")
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            color: Theme.textMuted
            horizontalAlignment: Text.AlignHCenter
        }
    }
}
