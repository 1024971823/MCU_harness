pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Rectangle {
    id: root
    property int activePage: 0
    property bool sidebarVisible: true
    property bool bottomPanelVisible: false
    property int pendingApprovalsCount: 0

    signal pageSelected(int index)
    signal toggleSidebar()
    signal toggleBottomPanel()
    signal openCommandPalette()

    implicitWidth: Theme.activityBarWidth
    color: Theme.bgSidebar

    readonly property var navItems: [
        { id: "agent",    icon: Icons.agent,    label: qsTr("Agent"),    shortcut: "Ctrl+1" },
        { id: "hardware", icon: Icons.hardware, label: qsTr("Hardware"), shortcut: "Ctrl+2" },
        { id: "watch",    icon: Icons.watch,    label: qsTr("Watch"),    shortcut: "Ctrl+3" },
        { id: "rtos",     icon: Icons.rtos,     label: qsTr("RTOS"),     shortcut: "Ctrl+4" },
        { id: "sessions", icon: Icons.sessions, label: qsTr("Sessions"), shortcut: "Ctrl+5" },
        { id: "audit",    icon: Icons.audit,    label: qsTr("Audit"),    shortcut: "Ctrl+6" }
    ]

    Divider {
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        orientation: Qt.Vertical
        width: 1
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: Theme.s2
        anchors.bottomMargin: Theme.s2
        spacing: Theme.s1

        // App Brand Icon
        Item {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 36
            Layout.preferredHeight: 36

            Image {
                anchors.centerIn: parent
                width: 26
                height: 26
                source: "qrc:/qt/qml/McuStudio/resources/app/mcu-harness-studio.svg"
                sourceSize: Qt.size(26, 26)
                smooth: true
                mipmap: true
                onStatusChanged: {
                    if (status === Image.Error) {
                        fallbackIcon.visible = true
                    }
                }
            }

            Icon {
                id: fallbackIcon
                anchors.centerIn: parent
                visible: false
                name: Icons.microchip
                size: 22
                color: Theme.accent
            }
        }

        Divider {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.s2
            Layout.rightMargin: Theme.s2
            Layout.topMargin: Theme.s1
            Layout.bottomMargin: Theme.s1
        }

        // Primary 6 rail buttons
        Repeater {
            model: root.navItems

            delegate: Item {
                id: railBtn
                required property var modelData
                required property int index

                readonly property bool isActive: root.activePage === index

                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 40
                Layout.preferredHeight: 40

                Rectangle {
                    anchors.left: parent.left
                    anchors.leftMargin: -4
                    anchors.verticalCenter: parent.verticalCenter
                    width: 3
                    height: railBtn.isActive ? 22 : 0
                    radius: 2
                    color: Theme.accent
                    Behavior on height { NumberAnimation { duration: Theme.animFast; easing.type: Theme.easingType } }
                }

                IconButton {
                    anchors.centerIn: parent
                    buttonSize: 36
                    iconSize: 18
                    iconName: railBtn.modelData.icon
                    active: railBtn.isActive
                    tooltipText: railBtn.modelData.label
                    shortcutText: railBtn.modelData.shortcut
                    onClicked: root.pageSelected(railBtn.index)
                }

                // Small badge dot for pending HITL approvals on Agent
                Rectangle {
                    visible: railBtn.index === 0 && root.pendingApprovalsCount > 0
                    anchors.top: parent.top
                    anchors.right: parent.right
                    anchors.topMargin: 4
                    anchors.rightMargin: 4
                    width: 8
                    height: 8
                    radius: 4
                    color: Theme.danger
                }
            }
        }

        Item { Layout.fillHeight: true }

        // Bottom workspace tools
        IconButton {
            Layout.alignment: Qt.AlignHCenter
            buttonSize: 36
            iconSize: 17
            iconName: Icons.panelLeft
            active: root.sidebarVisible
            tooltipText: qsTr("Toggle Sidebar")
            shortcutText: "Ctrl+B"
            onClicked: root.toggleSidebar()
        }

        IconButton {
            Layout.alignment: Qt.AlignHCenter
            buttonSize: 36
            iconSize: 17
            iconName: Icons.panelBottom
            active: root.bottomPanelVisible
            tooltipText: qsTr("Toggle Console")
            shortcutText: "Ctrl+J"
            onClicked: root.toggleBottomPanel()
        }

        IconButton {
            Layout.alignment: Qt.AlignHCenter
            buttonSize: 36
            iconSize: 17
            iconName: Icons.command
            tooltipText: qsTr("Command Palette")
            shortcutText: "Ctrl+K"
            onClicked: root.openCommandPalette()
        }

        IconButton {
            Layout.alignment: Qt.AlignHCenter
            buttonSize: 36
            iconSize: 18
            iconName: Icons.settings
            active: root.activePage === 6
            tooltipText: qsTr("Settings")
            shortcutText: "Ctrl+,"
            onClicked: root.pageSelected(6)
        }
    }
}
