pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Rectangle {
    id: root
    color: Theme.bgSidebar

    signal closeRequested()

    property string activeTab: "rtt" // "rtt" | "uart" | "engine"
    property string filterText: ""
    property bool autoScroll: true

    readonly property var rttObj: Workbench.rtt || ({})
    readonly property var uartObj: Workbench.uart || ({})

    Divider {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 1
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: 1
        spacing: 0

        // Header toolbar
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 38
            color: Theme.bgSidebar

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.s3
                anchors.rightMargin: Theme.s2
                spacing: Theme.s3

                TabBarLine {
                    id: tabBar
                    Layout.fillHeight: true
                    currentId: root.activeTab
                    tabs: [
                        { id: "rtt", label: qsTr("RTT"), icon: Icons.activity, badge: root.rttObj.running ? qsTr("LIVE") : "" },
                        { id: "uart", label: qsTr("UART"), icon: Icons.terminal, badge: root.uartObj.running ? qsTr("LIVE") : "" },
                        { id: "engine", label: qsTr("Engine"), icon: Icons.squareTerminal }
                    ]
                    onTabSelected: function(tabId) {
                        root.activeTab = tabId
                    }
                }

                Item { Layout.fillWidth: true }

                AppTextField {
                    Layout.preferredWidth: 190
                    leadingIcon: Icons.filter
                    placeholderText: qsTr("Filter logs...")
                    clearable: true
                    text: root.filterText
                    onTextEdited: root.filterText = text
                }

                // Context action per tab
                AppButton {
                    visible: root.activeTab === "rtt"
                    buttonSize: "sm"
                    variant: root.rttObj.running ? "danger" : "primary"
                    iconName: root.rttObj.running ? Icons.stop : Icons.play
                    text: root.rttObj.running ? qsTr("Stop RTT") : qsTr("Start RTT")
                    onClicked: {
                        if (root.rttObj.running) Workbench.rttStop()
                        else Workbench.rttStart(0)
                    }
                }

                AppButton {
                    visible: root.activeTab === "uart"
                    buttonSize: "sm"
                    variant: root.uartObj.running ? "danger" : "primary"
                    iconName: root.uartObj.running ? Icons.stop : Icons.play
                    text: root.uartObj.running ? qsTr("Stop UART") : qsTr("Start UART")
                    onClicked: {
                        if (root.uartObj.running) Workbench.uartStop()
                        else Workbench.uartStart(Settings.uartPort || "", Settings.uartBaudrate || 115200)
                    }
                }

                AppButton {
                    visible: root.activeTab === "engine"
                    buttonSize: "sm"
                    variant: "secondary"
                    iconName: Icons.rotateCcw
                    text: qsTr("Restart")
                    onClicked: Workbench.restartEngine()
                }

                IconButton {
                    buttonSize: 26
                    iconSize: 14
                    iconName: Icons.pin
                    active: root.autoScroll
                    tooltipText: qsTr("Auto-scroll")
                    onClicked: root.autoScroll = !root.autoScroll
                }

                IconButton {
                    buttonSize: 26
                    iconSize: 14
                    iconName: Icons.trash
                    tooltipText: qsTr("Clear")
                    onClicked: {
                        if (root.activeTab === "rtt") Workbench.clearRttLogs()
                        else if (root.activeTab === "uart") Workbench.clearUartLogs()
                        else Workbench.clearEngineLogs()
                    }
                }

                IconButton {
                    buttonSize: 26
                    iconSize: 14
                    iconName: Icons.close
                    tooltipText: qsTr("Hide Console")
                    shortcutText: "Ctrl+J"
                    onClicked: root.closeRequested()
                }
            }
        }

        Divider { Layout.fillWidth: true }

        // Log stream area
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.codeBg

            ListView {
                id: logList
                anchors.fill: parent
                anchors.margins: Theme.s2
                clip: true
                model: {
                    if (root.activeTab === "rtt") return Workbench.rttLogModel
                    if (root.activeTab === "uart") return Workbench.uartLogModel
                    return Workbench.engineLogModel
                }
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
                    property string level: ""

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
                                var t = logLine.text.toLowerCase()
                                if (t.indexOf("error") !== -1 || t.indexOf("fault") !== -1) return Theme.danger
                                if (t.indexOf("warn") !== -1) return Theme.warning
                                return Theme.textPrimary
                            }
                            wrapMode: TextEdit.WrapAnywhere
                        }
                    }
                }
            }
        }

        // TX bar for RTT / UART
        Divider {
            visible: root.activeTab !== "engine"
            Layout.fillWidth: true
        }

        Rectangle {
            visible: root.activeTab !== "engine"
            Layout.fillWidth: true
            Layout.preferredHeight: 38
            color: Theme.bgSidebar

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.s3
                anchors.rightMargin: Theme.s3
                spacing: Theme.s2

                Text {
                    text: root.activeTab === "rtt" ? "RTT TX >" : "UART TX >"
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.textXs
                    font.weight: Theme.weightSemiBold
                    color: Theme.accent
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
                    text: qsTr("Send")
                    onClicked: txInput.accepted()
                }
            }
        }
    }
}
