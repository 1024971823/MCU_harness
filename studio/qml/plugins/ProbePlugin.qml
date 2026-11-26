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

    readonly property var targetObj: Workbench.target || ({})
    readonly property var probeObj: Workbench.probe || ({})

    property bool statusCardExpanded: true
    property string selectedBackend: probeObj.backend || Settings.probeBackend || "jlink"

    readonly property string probeStateLabel: {
        if (!probeObj.connected) return qsTr("Disconnected")
        return probeObj.halted ? qsTr("Halted") : qsTr("Running")
    }
    readonly property color probeStateHue: {
        if (!probeObj.connected) return Theme.hueSlate
        return probeObj.halted ? Theme.hueAmber : Theme.hueEmerald
    }

    ScrollView {
        id: scroll
        anchors.fill: parent
        clip: true
        ScrollBar.vertical: AppScrollBar {}
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

        Item {
            width: scroll.availableWidth
            implicitHeight: mainCol.implicitHeight + Theme.s4 * 2

            ColumnLayout {
                id: mainCol
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.margins: root.compact ? Theme.s3 : Theme.s5
                spacing: Theme.s4

                // Top Header Bar (full title shown only when expanded/wide; compact panel uses PanelHeader above)
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2 + 2

                    IconChip {
                        visible: !root.compact
                        icon: Icons.cpu
                        hue: Theme.hueAmber
                        size: 32
                        iconSize: 16
                    }

                    ColumnLayout {
                        visible: !root.compact
                        Layout.fillWidth: true
                        spacing: 1

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Probe & Target")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textLg
                            font.weight: Theme.weightSemiBold
                            color: Theme.textPrimary
                            elide: Text.ElideRight
                        }

                        Text {
                            Layout.fillWidth: true
                            text: (root.targetObj.device || qsTr("No target")) + " · " + (root.probeObj.interfaceName || root.probeObj["interface"] || "SWD")
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.textXs
                            color: Theme.textSecondary
                            elide: Text.ElideRight
                        }
                    }

                    Pill {
                        visible: root.compact
                        icon: Icons.cpu
                        hue: Theme.hueAmber
                        text: (root.targetObj.device || qsTr("No target")) + " · " + (root.probeObj.interfaceName || root.probeObj["interface"] || "SWD")
                        compact: true
                    }

                    Item {
                        visible: root.compact
                        Layout.fillWidth: true
                    }

                    Pill {
                        dot: true
                        dotPulsing: !!root.probeObj.connected && !root.probeObj.halted
                        hue: root.probeStateHue
                        text: root.probeStateLabel
                        compact: root.compact
                    }

                    IconButton {
                        buttonSize: 28
                        iconSize: 14
                        iconName: Icons.refresh
                        tooltipText: qsTr("Refresh Hardware Snapshot")
                        shortcutText: "F5"
                        onClicked: {
                            Workbench.refreshSnapshot()
                            root.toastRequested(qsTr("Hardware snapshot refreshed"), "info")
                        }
                    }
                }

                // Responsive 1-col (compact) or 2-col (wide) Grid
                GridLayout {
                    Layout.fillWidth: true
                    columns: root.compact ? 1 : 2
                    columnSpacing: Theme.s4
                    rowSpacing: Theme.s4

                    // Card 1: Debug Probe Control
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        Layout.alignment: Qt.AlignTop
                        implicitHeight: probeCardCol.implicitHeight + Theme.s4 * 2
                        radius: Theme.radiusLg + 2
                        color: Theme.bgSurface
                        border.color: Theme.alpha(Theme.hueAmber, 0.28)
                        border.width: 1

                        ColumnLayout {
                            id: probeCardCol
                            anchors.fill: parent
                            anchors.margins: Theme.s4
                            spacing: Theme.s3

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                IconChip {
                                    icon: Icons.plugZap
                                    hue: Theme.hueAmber
                                    size: 26
                                    iconSize: 14
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 1
                                    Text {
                                        Layout.fillWidth: true
                                        text: qsTr("Debug Probe")
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textMd
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.textPrimary
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: qsTr("SWD/JTAG attachment & Cortex-M core control")
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textXs
                                        color: Theme.textSecondary
                                        wrapMode: Text.WordWrap
                                    }
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s1 + 2

                                Text {
                                    text: qsTr("Probe Backend")
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textXs
                                    font.weight: Theme.weightMedium
                                    color: Theme.textSecondary
                                }

                                SegmentedControl {
                                    Layout.fillWidth: true
                                    compact: root.compact
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
                            }

                            KeyValueRow {
                                label: qsTr("Interface")
                                value: root.probeObj.interfaceName || root.probeObj["interface"] || "SWD"
                                monospace: true
                            }
                            KeyValueRow {
                                label: qsTr("Serial No.")
                                value: root.probeObj.serialNo || ""
                                monospace: true
                                copyable: true
                            }

                            Rectangle {
                                visible: !!(root.probeObj.error && root.probeObj.error.length > 0)
                                Layout.fillWidth: true
                                implicitHeight: errTxt.implicitHeight + Theme.s2 * 2
                                radius: Theme.radiusMd
                                color: Theme.dangerSubtle
                                border.color: Theme.alpha(Theme.danger, 0.35)
                                border.width: 1

                                Text {
                                    id: errTxt
                                    anchors.fill: parent
                                    anchors.margins: Theme.s2
                                    text: root.probeObj.error || ""
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textXs
                                    color: Theme.danger
                                    wrapMode: Text.WordWrap
                                }
                            }

                            Flow {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                AppButton {
                                    buttonSize: "sm"
                                    variant: root.probeObj.connected ? "secondary" : "primary"
                                    iconName: root.probeObj.connected ? Icons.unplug : Icons.plug
                                    text: root.probeObj.connected ? qsTr("Disconnect") : qsTr("Connect")
                                    onClicked: {
                                        if (root.probeObj.connected) {
                                            Workbench.probeDisconnect()
                                            root.toastRequested(qsTr("Probe disconnected"), "info")
                                        } else {
                                            Workbench.probeConnect(root.selectedBackend)
                                            root.toastRequested(qsTr("Connecting via %1...").arg(root.selectedBackend), "info")
                                        }
                                    }
                                }

                                AppButton {
                                    buttonSize: "sm"
                                    variant: "secondary"
                                    iconName: Icons.pause
                                    text: qsTr("Halt")
                                    enabled: !!root.probeObj.connected
                                    onClicked: Workbench.probeHalt()
                                }

                                AppButton {
                                    buttonSize: "sm"
                                    variant: "secondary"
                                    iconName: Icons.play
                                    text: qsTr("Resume")
                                    enabled: !!root.probeObj.connected
                                    onClicked: Workbench.probeResume()
                                }

                                AppButton {
                                    buttonSize: "sm"
                                    variant: "danger"
                                    iconName: Icons.rotateCcw
                                    text: qsTr("Reset")
                                    enabled: !!root.probeObj.connected
                                    onClicked: confirmResetDlg.open()
                                }
                            }
                        }
                    }

                    // Card 2: Target & Build Artifacts
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        Layout.alignment: Qt.AlignTop
                        implicitHeight: targetCardCol.implicitHeight + Theme.s4 * 2
                        radius: Theme.radiusLg + 2
                        color: Theme.bgSurface
                        border.color: Theme.borderSubtle
                        border.width: 1

                        ColumnLayout {
                            id: targetCardCol
                            anchors.fill: parent
                            anchors.margins: Theme.s4
                            spacing: Theme.s3

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                IconChip {
                                    icon: Icons.microchip
                                    hue: Theme.hueAmber
                                    size: 26
                                    iconSize: 14
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 1
                                    Text {
                                        Layout.fillWidth: true
                                        text: qsTr("Target & Artifacts")
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textMd
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.textPrimary
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: qsTr("Discovered MCU profile and firmware symbols")
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textXs
                                        color: Theme.textSecondary
                                        wrapMode: Text.WordWrap
                                    }
                                }

                                Badge {
                                    text: root.targetObj.buildSystem || "unknown"
                                    variant: "warning"
                                    compact: true
                                }
                            }

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

                            Divider { Layout.fillWidth: true }

                            // Artifact rows
                            Repeater {
                                model: [
                                    {
                                        ok: !!root.targetObj.elfExists,
                                        okText: qsTr("ELF OK"),
                                        missText: qsTr("ELF Missing"),
                                        missVariant: "warning",
                                        path: root.targetObj.elfPath || ""
                                    },
                                    {
                                        ok: !!root.targetObj.svdExists,
                                        okText: qsTr("SVD OK"),
                                        missText: qsTr("SVD Missing"),
                                        missVariant: "neutral",
                                        path: root.targetObj.svdPath || ""
                                    },
                                    {
                                        ok: !!root.targetObj.iocExists,
                                        okText: qsTr("IOC OK"),
                                        missText: qsTr("IOC Missing"),
                                        missVariant: "neutral",
                                        path: root.targetObj.iocPath || ""
                                    }
                                ]

                                delegate: RowLayout {
                                    id: artRow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    Badge {
                                        text: artRow.modelData.ok ? artRow.modelData.okText : artRow.modelData.missText
                                        variant: artRow.modelData.ok ? "success" : artRow.modelData.missVariant
                                        compact: true
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: artRow.modelData.path.length > 0 ? artRow.modelData.path : "—"
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textXs
                                        color: Theme.textSecondary
                                        elide: Text.ElideMiddle
                                    }

                                    IconButton {
                                        visible: artRow.modelData.path.length > 0
                                        buttonSize: 22
                                        iconSize: 12
                                        iconName: Icons.copy
                                        tooltipText: qsTr("Copy path")
                                        onClicked: {
                                            Utils.copyToClipboard(artRow.modelData.path)
                                            root.toastRequested(qsTr("Copied path"), "info")
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // Card 3: Pin & Peripheral Health Overview
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.columnSpan: root.compact ? 1 : 2
                        implicitHeight: healthCol.implicitHeight + Theme.s4 * 2
                        radius: Theme.radiusLg + 2
                        color: Theme.bgSurface
                        border.color: Theme.borderSubtle
                        border.width: 1

                        ColumnLayout {
                            id: healthCol
                            anchors.fill: parent
                            anchors.margins: Theme.s4
                            spacing: Theme.s3

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                IconChip {
                                    icon: Icons.circuitBoard
                                    hue: Theme.hueAmber
                                    size: 26
                                    iconSize: 14
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: qsTr("Pin & Peripheral Health Overview")
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textMd
                                    font.weight: Theme.weightSemiBold
                                    color: Theme.textPrimary
                                    elide: Text.ElideRight
                                }
                            }

                            Flow {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                Pill {
                                    dot: true
                                    hue: root.probeObj.connected ? Theme.hueEmerald : Theme.hueAmber
                                    icon: Icons.plugZap
                                    text: root.probeObj.connected ? qsTr("SWD Link Active") : qsTr("SWD Unattached")
                                }
                                Pill {
                                    dot: true
                                    hue: root.targetObj.elfExists ? Theme.hueEmerald : Theme.hueAmber
                                    icon: Icons.fileCode
                                    text: root.targetObj.elfExists ? qsTr("DWARF Symbols Ready") : qsTr("ELF Symbols Missing")
                                }
                                Pill {
                                    dot: true
                                    hue: root.targetObj.svdExists ? Theme.hueCyan : Theme.hueSlate
                                    icon: Icons.cpu
                                    text: root.targetObj.svdExists ? qsTr("CMSIS-SVD Decoded") : qsTr("No SVD Map")
                                }
                                Pill {
                                    dot: true
                                    hue: root.targetObj.iocExists ? Theme.hueAmber : Theme.hueSlate
                                    icon: Icons.waypoints
                                    text: root.targetObj.iocExists ? qsTr("CubeMX Pinout Loaded") : qsTr("No IOC Pinout")
                                }
                            }
                        }
                    }

                    // Card 4: Collapsible Hardware Status Card
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.columnSpan: root.compact ? 1 : 2
                        implicitHeight: statusCol.implicitHeight + Theme.s4 * 2
                        radius: Theme.radiusLg + 2
                        color: Theme.bgSurface
                        border.color: Theme.borderSubtle
                        border.width: 1

                        ColumnLayout {
                            id: statusCol
                            anchors.fill: parent
                            anchors.margins: Theme.s4
                            spacing: Theme.s3

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                IconChip {
                                    icon: Icons.scrollText
                                    hue: Theme.hueAmber
                                    size: 26
                                    iconSize: 14
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 1
                                    Text {
                                        Layout.fillWidth: true
                                        text: qsTr("Hardware Status Card")
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textMd
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.textPrimary
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: qsTr("Live markdown context injected into the Agent prompt")
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textXs
                                        color: Theme.textSecondary
                                        wrapMode: Text.WordWrap
                                    }
                                }

                                IconButton {
                                    buttonSize: 26
                                    iconSize: 14
                                    iconName: Icons.copy
                                    tooltipText: qsTr("Copy Status Card")
                                    onClicked: {
                                        Utils.copyToClipboard(Workbench.statusCard || "")
                                        root.toastRequested(qsTr("Copied status card"), "success")
                                    }
                                }

                                IconButton {
                                    buttonSize: 26
                                    iconSize: 14
                                    iconName: root.statusCardExpanded ? Icons.chevronUp : Icons.chevronDown
                                    tooltipText: root.statusCardExpanded ? qsTr("Collapse") : qsTr("Expand")
                                    onClicked: root.statusCardExpanded = !root.statusCardExpanded
                                }
                            }

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
    }

    // Reset Confirmation Dialog
    AppDialog {
        id: confirmResetDlg
        dialogWidth: 440
        iconName: Icons.rotateCcw
        iconColor: Theme.hueAmber
        stripColor: Theme.hueAmber
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
                    root.toastRequested(qsTr("Target reset & halted"), "warning")
                    confirmResetDlg.close()
                }
            },
            AppButton {
                variant: "danger"
                text: qsTr("Reset & Run")
                onClicked: {
                    Workbench.probeReset(false)
                    root.toastRequested(qsTr("Target reset & running"), "info")
                    confirmResetDlg.close()
                }
            }
        ]
    }
}
