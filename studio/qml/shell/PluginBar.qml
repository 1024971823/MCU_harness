pragma ComponentBehavior: Bound
import QtQuick
import McuStudio
import "../components"

Rectangle {
    id: root
    property string activePlugin: ""
    property bool pluginPanelVisible: false

    signal pluginClicked(string pluginId)
    signal openPluginsPageRequested()

    implicitWidth: Theme.pluginBarWidth
    color: Theme.bgSidebar

    readonly property var enabledPluginList: {
        var out = []
        var list = PluginRegistry.plugins
        for (var i = 0; i < list.length; ++i) {
            if (PluginRegistry.isEnabled(list[i].id)) {
                out.push(list[i])
            }
        }
        return out
    }

    property int _auditRevision: 0

    // Hidden proxy to count denied audit entries without fragile role enum numbers
    Item {
        visible: false
        Repeater {
            id: auditProxy
            model: Workbench.auditModel
            onCountChanged: root._auditRevision++
            delegate: Item {
                required property bool allowed
            }
        }
    }

    Connections {
        target: Workbench.auditModel
        function onDataChanged() { root._auditRevision++ }
        function onModelReset() { root._auditRevision++ }
        function onRowsInserted() { root._auditRevision++ }
        function onRowsRemoved() { root._auditRevision++ }
    }

    readonly property int rtosHazardCount: Workbench.rtosHazardsModel ? (Workbench.rtosHazardsModel.count || 0) : 0
    readonly property int auditDeniedCount: {
        var rev = root._auditRevision
        void(rev)
        var c = 0
        for (var i = 0; i < auditProxy.count; ++i) {
            var it = auditProxy.itemAt(i)
            if (it && !it["allowed"]) c++
        }
        return c
    }
    readonly property bool probeConnected: !!(Workbench.probe && Workbench.probe.connected)

    Divider {
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        orientation: Qt.Vertical
        width: 1
    }

    Column {
        anchors.top: parent.top
        anchors.topMargin: Theme.s3
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Theme.s2

        Repeater {
            model: root.enabledPluginList

            delegate: Item {
                id: pluginBtnWrap
                required property var modelData

                readonly property bool isActive: root.pluginPanelVisible && root.activePlugin === modelData.id
                readonly property int badgeCount: {
                    if (modelData.id === "rtos") return root.rtosHazardCount
                    if (modelData.id === "audit") return root.auditDeniedCount
                    return 0
                }

                width: root.width
                height: 36

                // 2px right-edge hue bar when active
                Rectangle {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    width: 2
                    height: pluginBtnWrap.isActive ? 20 : 0
                    radius: 1
                    color: pluginBtnWrap.modelData.hue

                    Behavior on height {
                        NumberAnimation {
                            duration: Theme.animationsEnabled ? Theme.animFast : 0
                            easing.type: Theme.easingType
                        }
                    }
                }

                Rectangle {
                    anchors.centerIn: parent
                    width: 36
                    height: 36
                    radius: Theme.radiusLg
                    color: pluginBtnWrap.isActive
                        ? Theme.alpha(pluginBtnWrap.modelData.hue, 0.16)
                        : (btnHover.hovered ? Theme.bgHover : "transparent")

                    Behavior on color {
                        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                    }

                    Icon {
                        anchors.centerIn: parent
                        name: pluginBtnWrap.modelData.icon
                        size: 18
                        color: pluginBtnWrap.isActive
                            ? pluginBtnWrap.modelData.hue
                            : (btnHover.hovered ? Theme.textPrimary : Theme.textMuted)
                    }

                    // Probe connected dot (emerald)
                    Rectangle {
                        visible: pluginBtnWrap.modelData.id === "probe" && root.probeConnected
                        anchors.top: parent.top
                        anchors.right: parent.right
                        anchors.topMargin: 2
                        anchors.rightMargin: 2
                        width: 8
                        height: 8
                        radius: 4
                        color: Theme.hueEmerald
                        border.color: Theme.bgSidebar
                        border.width: 1
                    }

                    // RTOS hazards / Audit denied count badge (rose)
                    Rectangle {
                        visible: pluginBtnWrap.badgeCount > 0
                        anchors.top: parent.top
                        anchors.right: parent.right
                        anchors.topMargin: 1
                        anchors.rightMargin: 1
                        width: Math.max(14, badgeTxt.implicitWidth + 6)
                        height: 14
                        radius: 7
                        color: Theme.hueRose

                        Text {
                            id: badgeTxt
                            anchors.centerIn: parent
                            text: pluginBtnWrap.badgeCount > 9 ? "9+" : String(pluginBtnWrap.badgeCount)
                            font.family: Theme.fontMono
                            font.pixelSize: 9
                            font.weight: Theme.weightBold
                            color: "#FFFFFF"
                        }
                    }

                    HoverHandler {
                        id: btnHover
                        cursorShape: Qt.PointingHandCursor
                    }
                    TapHandler {
                        onTapped: root.pluginClicked(pluginBtnWrap.modelData.id)
                    }

                    AppToolTip {
                        visible: btnHover.hovered
                        text: pluginBtnWrap.modelData.name
                    }
                }
            }
        }
    }

    // Bottom "+" button to manage plugins (Plugins page)
    Item {
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Theme.s3
        anchors.left: parent.left
        anchors.right: parent.right
        height: 36

        IconButton {
            anchors.centerIn: parent
            buttonSize: 36
            iconSize: 17
            iconName: Icons.plus
            tooltipText: qsTr("Manage Plugins")
            onClicked: root.openPluginsPageRequested()
        }
    }
}
