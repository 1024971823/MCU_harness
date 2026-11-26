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

    property string activeTab: "rtt" // "rtt" | "uart"
    property string filterText: ""
    property bool autoScroll: true

    readonly property var rttObj: Workbench.rtt || ({})
    readonly property var uartObj: Workbench.uart || ({})

    readonly property var baudList: ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: root.compact ? Theme.s3 : Theme.s5
        spacing: Theme.s3

        // Header + RTT/UART Tab Switcher
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            IconChip {
                icon: Icons.squareTerminal
                hue: Theme.hueSky
                size: 30
                iconSize: 15
            }

            TabBarLine {
                id: tabBar
                Layout.fillWidth: true
                currentId: root.activeTab
                tabs: [
                    { id: "rtt", label: qsTr("RTT"), icon: Icons.activity, badge: root.rttObj.running ? qsTr("LIVE") : "" },
                    { id: "uart", label: qsTr("UART"), icon: Icons.terminal, badge: root.uartObj.running ? qsTr("LIVE") : "" }
                ]
                onTabSelected: function(tabId) {
                    root.activeTab = tabId
                }
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
                iconName: Icons.trash
                tooltipText: qsTr("Clear logs")
                onClicked: {
                    if (root.activeTab === "rtt") {
                        Workbench.clearRttLogs()
                        root.toastRequested(qsTr("RTT logs cleared"), "info")
                    } else {
                        Workbench.clearUartLogs()
                        root.toastRequested(qsTr("UART logs cleared"), "info")
                    }
                }
            }
        }

        // Channel Configuration & Controls Card
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: ctrlCol.implicitHeight + Theme.s3 * 2
            radius: Theme.radiusLg
            color: Theme.bgSurface
            border.color: Theme.alpha(Theme.hueSky, 0.25)
            border.width: 1

            ColumnLayout {
                id: ctrlCol
                anchors.fill: parent
                anchors.margins: Theme.s3
                spacing: Theme.s2 + 2

                // RTT Controls
                RowLayout {
                    visible: root.activeTab === "rtt"
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("SEGGER RTT · Channel %1").arg(root.rttObj.channel || 0)
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textBase
                            font.weight: Theme.weightSemiBold
                            color: Theme.textPrimary
                            elide: Text.ElideRight
                        }

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("%1 lines · %2")
                                .arg(root.rttObj.bufferedLines || 0)
                                .arg(Utils.formatBytes(root.rttObj.bytesReceived || 0))
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.textXs
                            color: Theme.textSecondary
                            elide: Text.ElideRight
                        }
                    }

                    AppButton {
                        buttonSize: "sm"
                        variant: root.rttObj.running ? "danger" : "primary"
                        iconName: root.rttObj.running ? Icons.stop : Icons.play
                        text: root.rttObj.running ? qsTr("Stop RTT") : qsTr("Start RTT")
                        onClicked: {
                            if (root.rttObj.running) Workbench.rttStop()
                            else Workbench.rttStart(0)
                        }
                    }
                }

                // UART Controls (Responsive 1-col or 2-col)
                GridLayout {
                    visible: root.activeTab === "uart"
                    Layout.fillWidth: true
                    columns: root.compact ? 1 : 3
                    columnSpacing: Theme.s2
                    rowSpacing: Theme.s2

                    AppTextField {
                        id: uartPortField
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        monospace: true
                        placeholderText: "/dev/ttyACM0"
                        text: root.uartObj.port || Settings.uartPort || "/dev/ttyACM0"
                    }

                    RowLayout {
                        Layout.fillWidth: root.compact
                        spacing: Theme.s2

                        AppComboBox {
                            id: uartBaudBox
                            Layout.fillWidth: root.compact
                            Layout.preferredWidth: root.compact ? 1 : 118
                            model: root.baudList
                            currentIndex: {
                                var b = String(root.uartObj.baudrate || Settings.uartBaudrate || 115200)
                                var idx = root.baudList.indexOf(b)
                                return idx >= 0 ? idx : 4
                            }
                        }

                        AppButton {
                            buttonSize: "sm"
                            variant: root.uartObj.running ? "danger" : "primary"
                            iconName: root.uartObj.running ? Icons.stop : Icons.play
                            text: root.uartObj.running ? qsTr("Stop") : qsTr("Start")
                            onClicked: {
                                if (root.uartObj.running) Workbench.uartStop()
                                else Workbench.uartStart(uartPortField.text, parseInt(uartBaudBox.currentText, 10))
                            }
                        }
                    }
                }

                SearchField {
                    Layout.fillWidth: true
                    fieldHeight: 30
                    placeholderText: root.activeTab === "rtt"
                        ? qsTr("Filter RTT log stream...")
                        : qsTr("Filter UART log stream...")
                    text: root.filterText
                    onTextEdited: root.filterText = text
                    onCleared: root.filterText = ""
                }
            }
        }

        // Log Stream Viewport
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
                iconName: Icons.squareTerminal
                title: root.activeTab === "rtt" ? qsTr("No RTT Output") : qsTr("No UART Output")
                description: root.activeTab === "rtt"
                    ? qsTr("Start RTT capture to stream real-time target logs over SWD.")
                    : qsTr("Configure port & baudrate and click Start to stream serial telemetry.")
            }

            ListView {
                id: logList
                visible: count > 0
                anchors.fill: parent
                anchors.margins: Theme.s2 + 2
                clip: true
                spacing: 3
                model: root.activeTab === "rtt" ? Workbench.rttLogModel : Workbench.uartLogModel
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
                                if (lvl === "error" || t.indexOf("error") !== -1 || t.indexOf("fault") !== -1) return Theme.hueRose
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

        // Command Input Bar (TX)
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: txRow.implicitHeight + Theme.s2 * 2
            radius: Theme.radiusLg
            color: Theme.bgSurface
            border.color: Theme.borderSubtle
            border.width: 1

            RowLayout {
                id: txRow
                anchors.fill: parent
                anchors.margins: Theme.s2
                spacing: Theme.s2

                Pill {
                    hue: Theme.hueSky
                    text: root.activeTab === "rtt" ? "RTT >" : "UART >"
                    compact: true
                }

                AppTextField {
                    id: txInput
                    Layout.fillWidth: true
                    monospace: true
                    placeholderText: root.activeTab === "rtt"
                        ? qsTr("Send command to RTT channel 0...")
                        : qsTr("Send bytes to UART...")
                    onAccepted: {
                        if (text.trim().length === 0) return
                        if (root.activeTab === "rtt") Workbench.rttWrite(text + "\n")
                        else Workbench.uartWrite(text + "\n")
                        text = ""
                    }
                }

                AppButton {
                    buttonSize: "sm"
                    variant: "secondary"
                    iconName: Icons.send
                    text: root.compact ? "" : qsTr("Send")
                    tooltipText: qsTr("Send")
                    onClicked: txInput.accepted()
                }
            }
        }
    }
}
