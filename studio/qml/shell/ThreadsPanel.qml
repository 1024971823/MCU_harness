pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Rectangle {
    id: root
    signal newChatRequested()
    signal loadSessionRequested(string sessionId)
    signal openProbeRequested()

    implicitWidth: Theme.threadsPanelWidth
    color: Theme.bgSidebar

    property string searchQuery: ""
    property int _modelRevision: 0

    readonly property var targetObj: Workbench.target || ({})
    readonly property var probeObj: Workbench.probe || ({})

    // Hidden proxy to read SessionModel roles safely under ComponentBehavior: Bound
    Item {
        visible: false
        Repeater {
            id: sessionsProxy
            model: Workbench.sessionsModel
            onCountChanged: root._modelRevision++
            delegate: Item {
                required property string sessionId
                required property int messageCount
                required property double updatedAt
                required property string updatedAtText
                required property string relativeTime
                required property string preview
                required property bool isCurrent
            }
        }
    }

    Connections {
        target: Workbench.sessionsModel
        function onDataChanged() { root._modelRevision++ }
        function onModelReset() { root._modelRevision++ }
        function onRowsInserted() { root._modelRevision++ }
        function onRowsRemoved() { root._modelRevision++ }
    }

    Connections {
        target: Settings
        function onLanguageChanged() { root._modelRevision++ }
    }

    readonly property var groupedRows: {
        // Trigger dependency on revision, search, and active sessionId
        var rev = root._modelRevision
        var activeSid = Workbench.sessionId || ""
        var q = root.searchQuery.trim().toLowerCase()
        var lang = Settings.language
        void(rev); void(activeSid); void(lang)

        var now = new Date()
        var startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000.0
        var startYesterday = startToday - 86400.0

        var buckets = {
            today: [],
            yesterday: [],
            earlier: []
        }

        for (var i = 0; i < sessionsProxy.count; ++i) {
            var it = sessionsProxy.itemAt(i)
            if (!it) continue
            var sid = it["sessionId"] || ""
            var prev = it["preview"] || ""
            if (q.length > 0) {
                var hay = (sid + " " + prev).toLowerCase()
                if (hay.indexOf(q) === -1) continue
            }

            var upd = it["updatedAt"] || 0
            var relText = "-"
            if (upd > 0) {
                var delta = Math.max(0, Math.floor(now.getTime() / 1000.0 - upd))
                if (delta < 60) {
                    relText = qsTr("Just now")
                } else if (delta < 3600) {
                    relText = qsTr("%1m ago").arg(Math.floor(delta / 60))
                } else if (delta < 86400) {
                    relText = qsTr("%1h ago").arg(Math.floor(delta / 3600))
                } else {
                    relText = qsTr("%1d ago").arg(Math.floor(delta / 86400))
                }
            }

            var itemObj = {
                isHeader: false,
                sessionId: sid,
                title: prev.length > 0 ? prev : sid,
                preview: sid + " · " + qsTr("%1 msgs").arg(it["messageCount"] || 0),
                timestamp: relText,
                updatedAt: upd
            }

            if (itemObj.updatedAt >= startToday) {
                buckets.today.push(itemObj)
            } else if (itemObj.updatedAt >= startYesterday) {
                buckets.yesterday.push(itemObj)
            } else {
                buckets.earlier.push(itemObj)
            }
        }

        var rows = []
        if (buckets.today.length > 0) {
            rows.push({ isHeader: true, groupTitle: qsTr("Today"), sessionId: "", title: "", preview: "", timestamp: "" })
            for (var a = 0; a < buckets.today.length; ++a) rows.push(buckets.today[a])
        }
        if (buckets.yesterday.length > 0) {
            rows.push({ isHeader: true, groupTitle: qsTr("Yesterday"), sessionId: "", title: "", preview: "", timestamp: "" })
            for (var b = 0; b < buckets.yesterday.length; ++b) rows.push(buckets.yesterday[b])
        }
        if (buckets.earlier.length > 0) {
            rows.push({ isHeader: true, groupTitle: qsTr("Earlier"), sessionId: "", title: "", preview: "", timestamp: "" })
            for (var c = 0; c < buckets.earlier.length; ++c) rows.push(buckets.earlier[c])
        }
        return rows
    }

    Divider {
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        orientation: Qt.Vertical
        width: 1
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.rightMargin: 1
        spacing: 0

        // Top actions: New chat + Search
        ColumnLayout {
            Layout.fillWidth: true
            Layout.margins: Theme.s3
            spacing: Theme.s2

            GradientButton {
                Layout.fillWidth: true
                buttonSize: "md"
                iconName: Icons.squarePen
                text: qsTr("New chat")
                tooltipText: qsTr("Start a new chat")
                shortcutText: "Ctrl+N"
                onClicked: root.newChatRequested()
            }

            SearchField {
                Layout.fillWidth: true
                placeholderText: qsTr("Search threads...")
                text: root.searchQuery
                onTextEdited: root.searchQuery = text
                onCleared: root.searchQuery = ""
            }
        }

        Divider { Layout.fillWidth: true }

        // Grouped sessions list
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: threadsList
                anchors.fill: parent
                anchors.topMargin: Theme.s1
                anchors.bottomMargin: Theme.s1
                clip: true
                spacing: 2
                model: root.groupedRows
                ScrollBar.vertical: AppScrollBar {}

                delegate: Item {
                    id: rowWrap
                    required property var modelData

                    width: threadsList.width
                    height: modelData.isHeader ? 26 : 54

                    // Group header ("Today" / "Yesterday" / "Earlier")
                    Text {
                        visible: rowWrap.modelData.isHeader
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.leftMargin: Theme.s3
                        anchors.rightMargin: Theme.s3
                        anchors.bottomMargin: 4
                        text: rowWrap.modelData.groupTitle || ""
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textXs
                        font.weight: Theme.weightSemiBold
                        font.letterSpacing: 0.4
                        color: Theme.textMuted
                    }

                    // Session row
                    Rectangle {
                        id: sessBtn
                        visible: !rowWrap.modelData.isHeader
                        readonly property bool isActive: !rowWrap.modelData.isHeader && rowWrap.modelData.sessionId === Workbench.sessionId

                        anchors.fill: parent
                        anchors.leftMargin: Theme.s2
                        anchors.rightMargin: Theme.s2
                        radius: Theme.radiusMd + 1
                        color: isActive ? Theme.bgActive : (sessHover.hovered ? Theme.bgHover : "transparent")

                        Behavior on color {
                            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                        }

                        // 2px accent left bar on active session
                        Rectangle {
                            visible: sessBtn.isActive
                            anchors.left: parent.left
                            anchors.top: parent.top
                            anchors.bottom: parent.bottom
                            anchors.topMargin: 8
                            anchors.bottomMargin: 8
                            width: 2
                            radius: 1
                            color: Theme.accent
                        }

                        HoverHandler {
                            id: sessHover
                            cursorShape: Qt.PointingHandCursor
                        }
                        TapHandler {
                            enabled: !rowWrap.modelData.isHeader
                            onTapped: root.loadSessionRequested(rowWrap.modelData.sessionId)
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme.s3
                            anchors.rightMargin: Theme.s2 + 2
                            anchors.topMargin: 7
                            anchors.bottomMargin: 7
                            spacing: 3

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                Text {
                                    Layout.fillWidth: true
                                    text: rowWrap.modelData.title || ""
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textSm + 1
                                    font.weight: sessBtn.isActive ? Theme.weightSemiBold : Theme.weightMedium
                                    color: Theme.textPrimary
                                    elide: Text.ElideRight
                                }

                                Text {
                                    text: rowWrap.modelData.timestamp || ""
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textMuted
                                }
                            }

                            Text {
                                Layout.fillWidth: true
                                text: rowWrap.modelData.preview || ""
                                font.family: Theme.fontMono
                                font.pixelSize: 10
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }

            Text {
                visible: threadsList.count === 0
                anchors.centerIn: parent
                width: parent.width - Theme.s6
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                text: root.searchQuery.trim().length > 0 ? qsTr("No matching threads") : qsTr("No conversations yet")
                font.family: Theme.fontSans
                font.pixelSize: Theme.textSm
                color: Theme.textMuted
            }
        }

        Divider { Layout.fillWidth: true }

        // Bottom pinned mini target card
        Rectangle {
            Layout.fillWidth: true
            Layout.margins: Theme.s3
            implicitHeight: targetCol.implicitHeight + Theme.s3 * 2
            radius: Theme.radiusLg
            color: Theme.bgSurface
            border.color: Theme.borderSubtle
            border.width: 1

            ColumnLayout {
                id: targetCol
                anchors.fill: parent
                anchors.margins: Theme.s3
                spacing: Theme.s1 + 2

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    StatusDot {
                        size: 7
                        color: root.probeObj.connected ? Theme.hueEmerald : Theme.textMuted
                        pulsing: !!root.probeObj.connected && root.probeObj.halted === false
                    }

                    Text {
                        Layout.fillWidth: true
                        text: root.targetObj.device || qsTr("No Target MCU")
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.textSm
                        font.weight: Theme.weightSemiBold
                        color: Theme.textPrimary
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    Text {
                        Layout.fillWidth: true
                        text: {
                            if (!root.probeObj.connected) return qsTr("Probe disconnected")
                            var st = root.probeObj.halted ? qsTr("Halted") : qsTr("Running")
                            return (root.probeObj.backend || "SWD") + " · " + st
                        }
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textXs
                        color: Theme.textMuted
                        elide: Text.ElideRight
                    }

                    Item {
                        implicitWidth: openProbeRow.implicitWidth
                        implicitHeight: openProbeRow.implicitHeight

                        HoverHandler {
                            id: openProbeHover
                            cursorShape: Qt.PointingHandCursor
                        }
                        TapHandler {
                            onTapped: root.openProbeRequested()
                        }

                        RowLayout {
                            id: openProbeRow
                            spacing: 2

                            Text {
                                text: qsTr("Open Probe")
                                font.family: Theme.fontSans
                                font.pixelSize: Theme.textXs
                                font.weight: Theme.weightSemiBold
                                color: openProbeHover.hovered ? Theme.accentHover : Theme.hueAmber
                            }

                            Icon {
                                name: Icons.chevronRight
                                size: 12
                                color: openProbeHover.hovered ? Theme.accentHover : Theme.hueAmber
                            }
                        }
                    }
                }
            }
        }
    }
}
