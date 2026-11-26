pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    property bool compact: width < 560
    signal toastRequested(string msg, string level)

    property string filterText: ""
    property bool autoScroll: true

    readonly property color statusHue: {
        if (Workbench.engineStatus === "ready") return Theme.hueEmerald
        if (Workbench.engineStatus === "starting") return Theme.hueAmber
        if (Workbench.engineStatus === "error") return Theme.hueRose
        return Theme.hueSlate
    }

    function copyAllLogs() {
        var model = Workbench.engineLogModel
        if (!model) return
        var lines = []
        var q = root.filterText.trim().toLowerCase()
        for (var i = 0; i < logList.count; ++i) {
            var idx = model.index(i, 0)
            var t = model.data(idx, Qt.UserRole + 3) || "" // timeText
            var txt = model.data(idx, Qt.UserRole + 4) || "" // text
            if (q.length === 0 || txt.toLowerCase().indexOf(q) !== -1) {
                lines.push((t ? t + " " : "") + txt)
            }
        }
        Utils.copyToClipboard(lines.join("\n"))
        root.toastRequested(qsTr("Copied %1 engine log lines").arg(lines.length), "success")
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.compact ? Theme.s3 : Theme.s5
        spacing: Theme.s3

        // Header (full title shown when maximized; compact panel relies on PanelHeader)
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2 + 2

            IconChip {
                visible: !root.compact
                icon: Icons.scrollText
                hue: Theme.hueSlate
                size: 32
                iconSize: 16
            }

            ColumnLayout {
                visible: !root.compact
                Layout.fillWidth: true
                spacing: 1

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Engine Logs")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textLg
                    font.weight: Theme.weightSemiBold
                    color: Theme.textPrimary
                    elide: Text.ElideRight
                }

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Agent runtime RPC & Python backend diagnostics")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textXs
                    color: Theme.textSecondary
                    elide: Text.ElideRight
                }
            }

            Text {
                visible: root.compact
                Layout.fillWidth: true
                text: qsTr("Agent runtime RPC & Python backend diagnostics")
                font.family: Theme.fontSans
                font.pixelSize: Theme.textXs
                color: Theme.textSecondary
                elide: Text.ElideRight
            }

            IconButton {
                buttonSize: 28
                iconSize: 14
                iconName: Icons.pin
                active: root.autoScroll
                tooltipText: qsTr("Auto-scroll")
                onClicked: root.autoScroll = !root.autoScroll
            }

            IconButton {
                buttonSize: 28
                iconSize: 14
                iconName: Icons.copy
                tooltipText: qsTr("Copy visible logs")
                onClicked: root.copyAllLogs()
            }

            IconButton {
                buttonSize: 28
                iconSize: 14
                iconName: Icons.trash
                tooltipText: qsTr("Clear Engine Logs")
                onClicked: {
                    Workbench.clearEngineLogs()
                    root.toastRequested(qsTr("Engine logs cleared"), "info")
                }
            }
        }

        // Engine Status & Provider Card
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: statusCol.implicitHeight + Theme.s3 * 2
            radius: Theme.radiusLg
            color: Theme.bgSurface
            border.color: Theme.borderSubtle
            border.width: 1

            ColumnLayout {
                id: statusCol
                anchors.fill: parent
                anchors.margins: Theme.s3
                spacing: Theme.s2

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    Flow {
                        Layout.fillWidth: true
                        spacing: Theme.s2

                        Pill {
                            dot: true
                            dotPulsing: Workbench.engineStatus === "starting"
                            hue: root.statusHue
                            text: qsTr("Engine: %1").arg(Workbench.engineStatus || "stopped")
                        }

                        Pill {
                            icon: Icons.sparkles
                            hue: Theme.hueSlate
                            text: (Workbench.provider || Settings.provider || "mock") + " · " + (Workbench.model || Settings.model || "default")
                        }
                    }

                    AppButton {
                        buttonSize: "sm"
                        variant: "secondary"
                        iconName: Icons.rotateCcw
                        text: root.compact ? qsTr("Restart") : qsTr("Restart Engine")
                        onClicked: {
                            Workbench.restartEngine()
                            root.toastRequested(qsTr("Restarting agent engine..."), "info")
                        }
                    }
                }

                Rectangle {
                    visible: !!(Workbench.engineError && Workbench.engineError.length > 0)
                    Layout.fillWidth: true
                    implicitHeight: errLabel.implicitHeight + Theme.s2 * 2
                    radius: Theme.radiusMd
                    color: Theme.dangerSubtle
                    border.color: Theme.alpha(Theme.danger, 0.35)
                    border.width: 1

                    Text {
                        id: errLabel
                        anchors.fill: parent
                        anchors.margins: Theme.s2
                        text: Workbench.engineError || ""
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.textXs
                        color: Theme.danger
                        wrapMode: Text.WordWrap
                    }
                }

                SearchField {
                    Layout.fillWidth: true
                    fieldHeight: 30
                    placeholderText: qsTr("Filter engine RPC logs...")
                    text: root.filterText
                    onTextEdited: root.filterText = text
                    onCleared: root.filterText = ""
                }
            }
        }

        // Log Stream
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusLg
            color: Theme.codeBg
            border.color: Theme.borderSubtle
            border.width: 1

            EmptyState {
                visible: logList.count === 0
                anchors.fill: parent
                iconName: Icons.scrollText
                title: qsTr("No Engine Logs")
                description: qsTr("Python RPC lifecycle events, stderr messages, and interlock diagnostics appear here.")
            }

            ListView {
                id: logList
                visible: count > 0
                anchors.fill: parent
                anchors.margins: Theme.s2 + 2
                clip: true
                spacing: 3
                model: Workbench.engineLogModel
                ScrollBar.vertical: AppScrollBar {}

                onCountChanged: {
                    if (root.autoScroll && count > 0) {
                        Qt.callLater(function() {
                            if (logList.count > 0) logList.positionViewAtEnd()
                        })
                    }
                }

                delegate: Item {
                    id: logLine
                    required property string text
                    required property string timeText
                    required property string level

                    readonly property bool matches: {
                        var q = root.filterText.trim().toLowerCase()
                        return q.length === 0 || text.toLowerCase().indexOf(q) !== -1
                    }

                    visible: matches
                    width: logList.width
                    height: visible ? lineRow.implicitHeight + 2 : 0

                    RowLayout {
                        id: lineRow
                        anchors.left: parent.left
                        anchors.right: parent.right
                        spacing: Theme.s2

                        Text {
                            Layout.alignment: Qt.AlignTop
                            text: logLine.timeText
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.textXs
                            color: Theme.textMuted
                        }

                        TextEdit {
                            Layout.fillWidth: true
                            readOnly: true
                            selectByMouse: true
                            text: logLine.text
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.textSm
                            color: {
                                var lvl = (logLine.level || "").toLowerCase()
                                var t = logLine.text.toLowerCase()
                                if (lvl === "error" || t.indexOf("error") !== -1 || t.indexOf("traceback") !== -1) return Theme.hueRose
                                if (lvl === "warn" || t.indexOf("warn") !== -1) return Theme.hueAmber
                                return Theme.textPrimary
                            }
                            selectionColor: Theme.accentSubtle
                            selectedTextColor: Theme.textPrimary
                            wrapMode: TextEdit.WrapAnywhere
                        }
                    }
                }
            }
        }
    }
}
