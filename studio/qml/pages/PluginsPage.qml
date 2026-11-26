pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    signal openPluginRequested(string pluginId)

    property string searchText: ""
    property string selectedCategory: "all"

    readonly property var categories: [
        { id: "all",           label: qsTr("All"),           hue: Theme.accent },
        { id: "hardware",      label: qsTr("Hardware"),      hue: Theme.hueAmber },
        { id: "debug",         label: qsTr("Debug"),         hue: Theme.hueEmerald },
        { id: "safety",        label: qsTr("Safety"),        hue: Theme.hueRose },
        { id: "observability", label: qsTr("Observability"), hue: Theme.hueCyan }
    ]

    function matchesPlugin(p) {
        if (selectedCategory !== "all" && p.category !== selectedCategory) return false
        var q = searchText.trim().toLowerCase()
        if (q.length === 0) return true
        if ((p.name || "").toLowerCase().indexOf(q) !== -1) return true
        if ((p.description || "").toLowerCase().indexOf(q) !== -1) return true
        if ((p.category || "").toLowerCase().indexOf(q) !== -1) return true
        var tags = p.tags || []
        for (var i = 0; i < tags.length; ++i) {
            if (String(tags[i]).toLowerCase().indexOf(q) !== -1) return true
        }
        return false
    }

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

                // Gradient Banner Header
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
                                icon: Icons.plugins
                                hue: Theme.hueCyan
                                size: 44
                                iconSize: 22
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 3

                                Text {
                                    Layout.fillWidth: true
                                    text: qsTr("Workbench Plugins")
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textXl
                                    font.weight: Theme.weightBold
                                    color: Theme.textPrimary
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: qsTr("Modular hardware telemetry, live variable sampling, RTOS inspection, and safety audit panels")
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textBase
                                    color: Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }
                            }

                            Pill {
                                icon: Icons.check
                                hue: Theme.hueEmerald
                                text: qsTr("%1 enabled").arg((Settings.enabledPlugins || []).length)
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s3

                            SearchField {
                                Layout.fillWidth: true
                                placeholderText: qsTr("Search plugins by name, capability, or tag...")
                                text: root.searchText
                                onTextEdited: root.searchText = text
                                onCleared: root.searchText = ""
                            }
                        }

                        Flow {
                            Layout.fillWidth: true
                            spacing: Theme.s2

                            Repeater {
                                model: root.categories

                                delegate: Chip {
                                    id: catChip
                                    required property var modelData
                                    text: modelData.label
                                    hue: modelData.hue
                                    checkable: false
                                    active: root.selectedCategory === modelData.id
                                    onClicked: root.selectedCategory = catChip.modelData.id
                                }
                            }
                        }
                    }
                }

                // Built-in Plugins Card Grid (min card width 280)
                GridLayout {
                    Layout.fillWidth: true
                    columns: Math.max(1, Math.floor((width + Theme.s4) / (280 + Theme.s4)))
                    columnSpacing: Theme.s4
                    rowSpacing: Theme.s4

                    Repeater {
                        model: PluginRegistry.plugins

                        delegate: Rectangle {
                            id: pluginCard
                            required property var modelData

                            readonly property bool enabledInSettings: PluginRegistry.isEnabled(modelData.id)
                            readonly property bool matches: root.matchesPlugin(modelData)

                            visible: matches
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            Layout.fillHeight: true
                            implicitHeight: cardCol.implicitHeight + Theme.s4 * 2 + 8
                            radius: Theme.radiusXl
                            color: Theme.bgSurface
                            border.color: cardHover.hovered ? modelData.hue : Theme.borderSubtle
                            border.width: 1

                            Behavior on border.color {
                                ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                            }
                            HoverHandler { id: cardHover }

                            // 6px top hue gradient strip inset cleanly inside the rounded border
                            Rectangle {
                                anchors.top: parent.top
                                anchors.topMargin: 1
                                anchors.left: parent.left
                                anchors.leftMargin: 1
                                anchors.right: parent.right
                                anchors.rightMargin: 1
                                height: 6
                                topLeftRadius: Theme.radiusXl - 1
                                topRightRadius: Theme.radiusXl - 1
                                bottomLeftRadius: 0
                                bottomRightRadius: 0
                                gradient: Gradient {
                                    orientation: Gradient.Horizontal
                                    GradientStop { position: 0.0; color: Theme.alpha(pluginCard.modelData.hue, 0.9) }
                                    GradientStop { position: 1.0; color: pluginCard.modelData.hue }
                                }
                            }

                            ColumnLayout {
                                id: cardCol
                                anchors.fill: parent
                                anchors.margins: Theme.s4
                                anchors.topMargin: Theme.s4 + 8
                                spacing: Theme.s3

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s3

                                    IconChip {
                                        icon: pluginCard.modelData.icon
                                        hue: pluginCard.modelData.hue
                                        size: 40
                                        iconSize: 20
                                    }

                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 3

                                        RowLayout {
                                            Layout.fillWidth: true
                                            spacing: Theme.s2

                                            Text {
                                                Layout.fillWidth: true
                                                text: pluginCard.modelData.name
                                                font.family: Theme.fontSans
                                                font.pixelSize: Theme.textMd
                                                font.weight: Theme.weightSemiBold
                                                color: Theme.textPrimary
                                                elide: Text.ElideRight
                                            }

                                            Badge {
                                                text: qsTr("Built-in")
                                                variant: "neutral"
                                                compact: true
                                            }
                                        }

                                        Text {
                                            text: "v1.0.0 · " + PluginRegistry.categoryLabel(pluginCard.modelData.category)
                                            font.family: Theme.fontMono
                                            font.pixelSize: Theme.textXs
                                            color: Theme.textMuted
                                        }
                                    }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: pluginCard.modelData.description
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textSm
                                    color: Theme.textSecondary
                                    wrapMode: Text.WordWrap
                                }

                                Flow {
                                    Layout.fillWidth: true
                                    spacing: Theme.s1 + 2

                                    Repeater {
                                        model: pluginCard.modelData.tags || []

                                        delegate: Chip {
                                            required property string modelData
                                            text: modelData
                                            hue: pluginCard.modelData.hue
                                            checkable: false
                                            active: false
                                        }
                                    }
                                }

                                Item { Layout.fillHeight: true }

                                Divider { Layout.fillWidth: true }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    AppSwitch {
                                        checked: pluginCard.enabledInSettings
                                        onToggled: Settings.setPluginEnabled(pluginCard.modelData.id, checked)
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: pluginCard.enabledInSettings ? qsTr("Enabled") : qsTr("Disabled")
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textSm
                                        color: pluginCard.enabledInSettings ? Theme.textPrimary : Theme.textMuted
                                    }

                                    AppButton {
                                        buttonSize: "sm"
                                        variant: "secondary"
                                        iconName: Icons.panelRightOpen
                                        text: qsTr("Open")
                                        onClicked: root.openPluginRequested(pluginCard.modelData.id)
                                    }
                                }
                            }
                        }
                    }
                }

                // Coming Soon Section
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s3

                    SectionHeader {
                        iconName: Icons.orbit
                        title: qsTr("Coming Soon")
                        subtitle: qsTr("Upcoming hardware observability and regression modules")
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: Math.max(1, Math.floor((width + Theme.s4) / (280 + Theme.s4)))
                        columnSpacing: Theme.s4
                        rowSpacing: Theme.s4

                        Repeater {
                            model: PluginRegistry.comingSoon

                            delegate: Rectangle {
                                id: soonCard
                                required property var modelData

                                visible: root.matchesPlugin(modelData)
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                Layout.fillHeight: true
                                implicitHeight: soonCol.implicitHeight + Theme.s4 * 2 + 8
                                radius: Theme.radiusXl
                                color: Theme.bgSurface
                                border.color: Theme.borderSubtle
                                border.width: 1
                                opacity: 0.72

                                // 6px top strip inset cleanly inside the rounded border
                                Rectangle {
                                    anchors.top: parent.top
                                    anchors.topMargin: 1
                                    anchors.left: parent.left
                                    anchors.leftMargin: 1
                                    anchors.right: parent.right
                                    anchors.rightMargin: 1
                                    height: 6
                                    topLeftRadius: Theme.radiusXl - 1
                                    topRightRadius: Theme.radiusXl - 1
                                    bottomLeftRadius: 0
                                    bottomRightRadius: 0
                                    gradient: Gradient {
                                        orientation: Gradient.Horizontal
                                        GradientStop { position: 0.0; color: Theme.alpha(soonCard.modelData.hue, 0.75) }
                                        GradientStop { position: 1.0; color: soonCard.modelData.hue }
                                    }
                                }

                                ColumnLayout {
                                    id: soonCol
                                    anchors.fill: parent
                                    anchors.margins: Theme.s4
                                    anchors.topMargin: Theme.s4 + 8
                                    spacing: Theme.s3

                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Theme.s3

                                        IconChip {
                                            icon: soonCard.modelData.icon
                                            hue: soonCard.modelData.hue
                                            size: 40
                                            iconSize: 20
                                        }

                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 3

                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: Theme.s2

                                                Text {
                                                    Layout.fillWidth: true
                                                    text: soonCard.modelData.name
                                                    font.family: Theme.fontSans
                                                    font.pixelSize: Theme.textMd
                                                    font.weight: Theme.weightSemiBold
                                                    color: Theme.textPrimary
                                                    elide: Text.ElideRight
                                                }

                                                Badge {
                                                    text: qsTr("Soon")
                                                    variant: "purple"
                                                    compact: true
                                                }
                                            }

                                            Text {
                                                text: PluginRegistry.categoryLabel(soonCard.modelData.category)
                                                font.family: Theme.fontMono
                                                font.pixelSize: Theme.textXs
                                                color: Theme.textMuted
                                            }
                                        }
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: soonCard.modelData.description
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textSm
                                        color: Theme.textSecondary
                                        wrapMode: Text.WordWrap
                                    }

                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: Theme.s1 + 2

                                        Repeater {
                                            model: soonCard.modelData.tags || []

                                            delegate: Chip {
                                                required property string modelData
                                                text: modelData
                                                hue: soonCard.modelData.hue
                                                checkable: false
                                                active: false
                                            }
                                        }
                                    }

                                    Item { Layout.fillHeight: true }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
