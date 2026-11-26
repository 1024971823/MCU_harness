import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    signal openToast(string message, string level)

    readonly property var targetObj: Workbench.target || ({})
    readonly property var probeObj: Workbench.probe || ({})
    readonly property var rttObj: Workbench.rtt || ({})
    readonly property var uartObj: Workbench.uart || ({})

    property bool statusCardExpanded: true
    property string selectedBackend: probeObj.backend || Settings.probeBackend || "jlink"

    ScrollView {
        anchors.fill: parent
        clip: true
        ScrollBar.vertical: AppScrollBar {}
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        Item {
            width: root.width
            implicitHeight: mainCol.implicitHeight + Theme.s6 * 2

            ColumnLayout {
                id: mainCol
                anchors.top: parent.top
                anchors.topMargin: Theme.s6
                anchors.horizontalCenter: parent.horizontalCenter
                width: Math.min(parent.width - Theme.s6 * 2, 1140)
                spacing: Theme.s5

                SectionHeader {
                    iconName: Icons.hardware
                    title: qsTr("Hardware & Debug Probe")
                    subtitle: qsTr("Zero-token hardware telemetry, ELF/SVD/IOC artifact validation, and probe lifecycle control")

                    AppButton {
                        buttonSize: "sm"
                        variant: "secondary"
                        iconName: Icons.refresh
                        text: qsTr("Refresh")
                        shortcutText: "F5"
                        onClicked: {
                            Workbench.refreshSnapshot()
                            root.openToast(qsTr("Hardware snapshot refreshed"), "info")
                        }
                    }
                }

                // Responsive 2-column -> 1-column Grid
                GridLayout {
                    Layout.fillWidth: true
                    columns: mainCol.width < 1000 ? 1 : 2
                    columnSpacing: Theme.s4
                    rowSpacing: Theme.s4

                    // Card 1: Target & Artifacts
                    Card {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        iconName: Icons.microchip
                        title: qsTr("Target & Build Artifacts")
                        subtitle: qsTr("Discovered manifest profile and firmware symbol files")

                        headerActions: [
                            Badge {
                                text: root.targetObj.buildSystem || "unknown"
                                variant: "accent"
                                compact: true
                            }
                        ]

                        KeyValueRow {
                            label: qsTr("Project Dir")
                            value: root.targetObj.projectDir || ""
                            monospace: true
                            middleElide: true
                            copyable: true
                        }
                        KeyValueRow {
                            label: qsTr("Device")
                            value: root.targetObj.device || ""
                            monospace: true
                        }
                        KeyValueRow {
                            label: qsTr("Core")
                            value: root.targetObj.core || ""
                            monospace: true
                        }

                        Divider { Layout.fillWidth: true; Layout.topMargin: Theme.s1; Layout.bottomMargin: Theme.s1 }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s2
                            Badge {
                                text: root.targetObj.elfExists ? qsTr("ELF OK") : qsTr("ELF Missing")
                                variant: root.targetObj.elfExists ? "success" : "warning"
                                compact: true
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.targetObj.elfPath || "—"
                                font.family: Theme.fontMono
                                font.pixelSize: Theme.textSm
                                color: Theme.textSecondary
                                elide: Text.ElideMiddle
                            }
                            IconButton {
                                visible: !!root.targetObj.elfPath
                                buttonSize: 22
                                iconSize: 12
                                iconName: Icons.copy
                                onClicked: Utils.copyToClipboard(root.targetObj.elfPath)
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s2
                            Badge {
                                text: root.targetObj.svdExists ? qsTr("SVD OK") : qsTr("SVD Missing")
                                variant: root.targetObj.svdExists ? "success" : "neutral"
                                compact: true
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.targetObj.svdPath || "—"
                                font.family: Theme.fontMono
                                font.pixelSize: Theme.textSm
                                color: Theme.textSecondary
                                elide: Text.ElideMiddle
                            }
                            IconButton {
                                visible: !!root.targetObj.svdPath
                                buttonSize: 22
                                iconSize: 12
                                iconName: Icons.copy
                                onClicked: Utils.copyToClipboard(root.targetObj.svdPath)
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s2
                            Badge {
                                text: root.targetObj.iocExists ? qsTr("IOC OK") : qsTr("IOC Missing")
                                variant: root.targetObj.iocExists ? "info" : "neutral"
                                compact: true
                            }
                            Text {
                                Layout.fillWidth: true
                                text: root.targetObj.iocPath || "—"
                                font.family: Theme.fontMono
                                font.pixelSize: Theme.textSm
                                color: Theme.textSecondary
                                elide: Text.ElideMiddle
                            }
                        }
                    }

                    // Card 2: Debug Probe Control
                    Card {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        iconName: Icons.plugZap
                        title: qsTr("Debug Probe")
                        subtitle: qsTr("Non-intrusive attachment and Cortex-M core execution control")

                        headerActions: [
                            Badge {
                                text: root.probeObj.connected ? (root.probeObj.halted ? qsTr("Halted") : qsTr("Running")) : qsTr("Disconnected")
                                variant: root.probeObj.connected ? (root.probeObj.halted ? "warning" : "success") : "neutral"
                                iconName: root.probeObj.connected ? Icons.circleDot : Icons.unplug
                            }
                        ]

                        Text {
                            text: qsTr("Probe Backend")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textSm
                            color: Theme.textSecondary
                        }

                        SegmentedControl {
                            Layout.fillWidth: true
                            currentValue: root.selectedBackend
                            items: [
                                { value: "jlink", label: "J-Link" },
                                { value: "openocd", label: "OpenOCD" },
                                { value: "probe-rs", label: "probe-rs" },
                                { value: "pyocd", label: "pyOCD" }
                            ]
                            onActivated: function(val) {
                                root.selectedBackend = val
                            }
                        }

                        KeyValueRow {
                            label: qsTr("Interface")
                            value: (root.probeObj.interfaceName || root.probeObj["interface"] || "SWD")
                            monospace: true
                        }
                        KeyValueRow {
                            label: qsTr("Serial No.")
                            value: root.probeObj.serialNo || ""
                            monospace: true
                            copyable: true
                        }

                        Text {
                            visible: !!(root.probeObj.error && root.probeObj.error.length > 0)
                            Layout.fillWidth: true
                            text: root.probeObj.error || ""
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textSm
                            color: Theme.danger
                            wrapMode: Text.WordWrap
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            Layout.topMargin: Theme.s1
                            spacing: Theme.s2

                            AppButton {
                                variant: root.probeObj.connected ? "secondary" : "primary"
                                iconName: root.probeObj.connected ? Icons.unplug : Icons.plug
                                text: root.probeObj.connected ? qsTr("Disconnect") : qsTr("Connect")
                                onClicked: {
                                    if (root.probeObj.connected) Workbench.probeDisconnect()
                                    else Workbench.probeConnect(root.selectedBackend)
                                }
                            }

                            AppButton {
                                variant: "secondary"
                                iconName: Icons.pause
                                text: qsTr("Halt")
                                enabled: !!root.probeObj.connected
                                onClicked: Workbench.probeHalt()
                            }

                            AppButton {
                                variant: "secondary"
                                iconName: Icons.play
                                text: qsTr("Resume")
                                enabled: !!root.probeObj.connected
                                onClicked: Workbench.probeResume()
                            }

                            AppButton {
                                variant: "danger"
                                iconName: Icons.rotateCcw
                                text: qsTr("Reset")
                                enabled: !!root.probeObj.connected
                                onClicked: confirmResetDlg.open()
                            }
                        }
                    }

                    // Card 3: RTT & UART Channels
                    Card {
                        Layout.fillWidth: true
                        Layout.columnSpan: mainCol.width < 1000 ? 1 : 2
                        iconName: Icons.terminal
                        title: qsTr("RTT & UART Streaming Channels")
                        subtitle: qsTr("Background lock-free ring buffers capturing target firmware telemetry")

                        GridLayout {
                            Layout.fillWidth: true
                            columns: mainCol.width < 820 ? 1 : 2
                            columnSpacing: Theme.s5
                            rowSpacing: Theme.s3

                            // RTT Column
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2
                                    Icon { name: Icons.activity; size: 15; color: Theme.accent }
                                    Text {
                                        Layout.fillWidth: true
                                        text: qsTr("SEGGER RTT (Channel %1)").arg(root.rttObj.channel || 0)
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textBase
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.textPrimary
                                    }
                                    Badge {
                                        text: root.rttObj.running ? qsTr("Running") : qsTr("Stopped")
                                        variant: root.rttObj.running ? "success" : "neutral"
                                        compact: true
                                    }
                                }

                                KeyValueRow {
                                    label: qsTr("Buffered")
                                    value: qsTr("%1 lines (%2)").arg(root.rttObj.bufferedLines || 0).arg(Utils.formatBytes(root.rttObj.bytesReceived || 0))
                                    monospace: true
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

                            // UART Column
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2
                                    Icon { name: Icons.waypoints; size: 15; color: Theme.info }
                                    Text {
                                        Layout.fillWidth: true
                                        text: qsTr("Serial UART")
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textBase
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.textPrimary
                                    }
                                    Badge {
                                        text: root.uartObj.running ? qsTr("Running") : qsTr("Stopped")
                                        variant: root.uartObj.running ? "success" : "neutral"
                                        compact: true
                                    }
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    AppTextField {
                                        id: uartPortField
                                        Layout.fillWidth: true
                                        monospace: true
                                        placeholderText: "/dev/ttyACM0"
                                        text: root.uartObj.port || Settings.uartPort || "/dev/ttyACM0"
                                    }

                                    AppComboBox {
                                        id: uartBaudBox
                                        Layout.preferredWidth: 118
                                        model: ["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]
                                        currentIndex: 4
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
                        }
                    }

                    // Card 4: Collapsible Status Card
                    Card {
                        Layout.fillWidth: true
                        Layout.columnSpan: mainCol.width < 1000 ? 1 : 2
                        iconName: Icons.scrollText
                        title: qsTr("Hardware Status Card")
                        subtitle: qsTr("Live markdown context injected into the Agent system prompt")

                        headerActions: [
                            IconButton {
                                buttonSize: 26
                                iconSize: 14
                                iconName: Icons.copy
                                tooltipText: qsTr("Copy Status Card")
                                onClicked: {
                                    Utils.copyToClipboard(Workbench.statusCard || "")
                                    root.openToast(qsTr("Copied status card"), "success")
                                }
                            },
                            IconButton {
                                buttonSize: 26
                                iconSize: 14
                                iconName: root.statusCardExpanded ? Icons.chevronUp : Icons.chevronDown
                                tooltipText: root.statusCardExpanded ? qsTr("Collapse") : qsTr("Expand")
                                onClicked: root.statusCardExpanded = !root.statusCardExpanded
                            }
                        ]

                        MarkdownText {
                            visible: root.statusCardExpanded
                            Layout.fillWidth: true
                            text: Workbench.statusCard || qsTr("_No status card available yet._")
                        }
                    }
                }
            }
        }
    }

    // Reset Confirmation Dialog
    AppDialog {
        id: confirmResetDlg
        dialogWidth: 440
        iconName: Icons.rotateCcw
        iconColor: Theme.warning
        title: qsTr("Reset Target MCU?")
        subtitle: qsTr("Confirm whether to perform a soft reset or reset and halt at the vector table.")

        Text {
            Layout.fillWidth: true
            text: qsTr("Resetting the microcontroller will restart firmware execution and may interrupt active peripheral DMA or PWM outputs.")
            font.family: Theme.fontSans
            font.pixelSize: Theme.textBase
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
        }

        footer: [
            Item { Layout.fillWidth: true },
            AppButton {
                variant: "ghost"
                text: qsTr("Cancel")
                onClicked: confirmResetDlg.close()
            },
            AppButton {
                variant: "secondary"
                text: qsTr("Reset & Halt")
                onClicked: {
                    Workbench.probeReset(true)
                    confirmResetDlg.close()
                }
            },
            AppButton {
                variant: "danger"
                text: qsTr("Reset & Run")
                onClicked: {
                    Workbench.probeReset(false)
                    confirmResetDlg.close()
                }
            }
        ]
    }
}
