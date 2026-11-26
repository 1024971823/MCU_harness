pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"
import "../plugins"

Rectangle {
    id: root
    property string activePlugin: "probe"
    property bool panelOpen: false

    signal maximizeRequested()
    signal closeRequested()
    signal toastRequested(string msg, string level)

    function clampWidth(w) {
        return Math.max(320, Math.min(720, Math.round(w)))
    }

    property int targetWidth: clampWidth(Settings.pluginPanelWidth || Theme.pluginPanelDefaultWidth)
    readonly property int desiredWidth: panelOpen ? targetWidth : 0
    property int animatedWidth: desiredWidth

    Behavior on animatedWidth {
        enabled: Theme.animationsEnabled && !resizeHandle.active
        NumberAnimation {
            duration: 220
            easing.type: Theme.easingType
        }
    }

    Connections {
        target: Settings
        function onPluginPanelWidthChanged() {
            if (!resizeHandle.active) {
                root.targetWidth = root.clampWidth(Settings.pluginPanelWidth || Theme.pluginPanelDefaultWidth)
            }
        }
    }

    Layout.preferredWidth: animatedWidth
    visible: animatedWidth > 0
    clip: true
    color: Theme.bgPanel

    readonly property var pluginMeta: PluginRegistry.byId(activePlugin) || PluginRegistry.plugins[0]

    // Internal Component declarations for the 6 built-in plugins
    Component { id: probeComp; ProbePlugin {} }
    Component { id: watchComp; WatchPlugin {} }
    Component { id: rtosComp; RtosPlugin {} }
    Component { id: consoleComp; ConsolePlugin {} }
    Component { id: auditComp; AuditPlugin {} }
    Component { id: engineComp; EngineLogPlugin {} }

    function componentForPlugin(id) {
        switch (id) {
        case "probe":   return probeComp
        case "watch":   return watchComp
        case "rtos":    return rtosComp
        case "console": return consoleComp
        case "audit":   return auditComp
        case "engine":  return engineComp
        default:        return probeComp
        }
    }

    ResizeHandle {
        id: resizeHandle
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 4
        z: 2
        orientation: Qt.Vertical

        onDraggedDelta: function(delta) {
            // Left handle: dragging left (negative delta) widens the right-side panel
            root.targetWidth = root.clampWidth(root.targetWidth - delta)
        }
        onDragFinished: {
            Settings.setPluginPanelWidth(root.targetWidth)
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 4
        spacing: 0

        PanelHeader {
            Layout.fillWidth: true
            icon: root.pluginMeta ? root.pluginMeta.icon : Icons.cpu
            hue: root.pluginMeta ? root.pluginMeta.hue : Theme.hueAmber
            title: root.pluginMeta ? root.pluginMeta.name : qsTr("Plugin")
            maximizable: true
            maximized: false
            closable: true
            onMaximizeRequested: root.maximizeRequested()
            onCloseRequested: root.closeRequested()
        }

        Loader {
            id: pluginLoader
            Layout.fillWidth: true
            Layout.fillHeight: true
            active: root.visible && root.activePlugin.length > 0
            sourceComponent: root.componentForPlugin(root.activePlugin)
        }
    }

    Connections {
        target: pluginLoader.item
        ignoreUnknownSignals: true
        function onToastRequested(msg, level) {
            root.toastRequested(msg, level)
        }
    }
}
