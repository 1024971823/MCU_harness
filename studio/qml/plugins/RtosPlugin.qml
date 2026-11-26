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

    readonly property var rtosObj: Workbench.rtos || ({})
    readonly property bool hasRtos: !!(rtosObj.rtos && rtosObj.rtos.length > 0 && !rtosObj.error)

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

                // Header (full title shown when maximized; compact panel relies on PanelHeader)
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.s2 + 2

                    IconChip {
                        visible: !root.compact
                        icon: Icons.layers
                        hue: Theme.hueEmerald
                        size: 32
                        iconSize: 16
                    }

                    ColumnLayout {
                        visible: !root.compact
                        Layout.fillWidth: true
                        spacing: 1

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("RTOS Kernel & Tasks")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textLg
                            font.weight: Theme.weightSemiBold
                            color: Theme.textPrimary
                            elide: Text.ElideRight
                        }

                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Task states, stack watermarks & hazard checks")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textXs
                            color: Theme.textSecondary
                            elide: Text.ElideRight
                        }
                    }

                    Text {
                        visible: root.compact
                        Layout.fillWidth: true
                        text: qsTr("Task states, stack watermarks & hazard checks")
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textXs
                        color: Theme.textSecondary
                        elide: Text.ElideRight
                    }

                    IconButton {
                        buttonSize: 28
                        iconSize: 14
                        iconName: Icons.refresh
                        tooltipText: qsTr("Refresh RTOS Telemetry")
                        onClicked: {
                            Workbench.refreshSnapshot()
                            root.toastRequested(qsTr("RTOS telemetry refreshed"), "info")
                        }
                    }
                }

                // Kernel & Tick Summary Pills
                Flow {
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    Pill {
                        icon: Icons.layers
                        hue: root.hasRtos ? Theme.hueEmerald : Theme.hueSlate
                        text: qsTr("Kernel: %1").arg(root.rtosObj.rtos || qsTr("None"))
                    }
                    Pill {
                        dot: true
                        dotPulsing: root.rtosObj.schedulerRunning === true
                        hue: root.rtosObj.schedulerRunning === true ? Theme.hueEmerald : Theme.hueSlate
                        text: root.rtosObj.schedulerRunning === true
                            ? qsTr("Scheduler: Running")
                            : (root.rtosObj.schedulerRunning === false ? qsTr("Scheduler: Suspended") : qsTr("Scheduler: —"))
                    }
                    Pill {
                        icon: Icons.timer
                        hue: Theme.hueCyan
                        text: qsTr("Tick: %1").arg(root.rtosObj.tickCount !== undefined && root.rtosObj.tickCount !== null ? root.rtosObj.tickCount : "—")
                    }
                    Pill {
                        icon: Icons.circleDot
                        hue: root.rtosObj.currentTask ? Theme.hueEmerald : Theme.hueSlate
                        text: qsTr("Current: %1").arg(root.rtosObj.currentTask || "—")
                    }
                    Pill {
                        icon: Icons.listChecks
                        hue: Theme.hueSlate
                        text: qsTr("Tasks: %1").arg(taskRep.count)
                    }
                }

                // Hazard Alert Cards
                ColumnLayout {
                    visible: hazardRep.count > 0
                    Layout.fillWidth: true
                    spacing: Theme.s2

                    Repeater {
                        id: hazardRep
                        model: Workbench.rtosHazardsModel

                        delegate: Rectangle {
                            id: hazCard
                            required property string severity
                            required property string code
                            required property string taskName
                            required property string message

                            readonly property bool isCritical: severity === "critical" || severity === "error"
                            readonly property color hazHue: isCritical ? Theme.hueRose : Theme.hueAmber

                            Layout.fillWidth: true
                            implicitHeight: hazCol.implicitHeight + Theme.s3 * 2
                            radius: Theme.radiusLg
                            color: Theme.alpha(hazHue, Theme.isDark ? 0.12 : 0.09)
                            border.color: Theme.alpha(hazHue, 0.55)
                            border.width: 1

                            ColumnLayout {
                                id: hazCol
                                anchors.fill: parent
                                anchors.margins: Theme.s3
                                spacing: Theme.s1 + 2

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    Icon {
                                        name: Icons.triangleAlert
                                        size: 15
                                        color: hazCard.hazHue
                                    }

                                    Badge {
                                        text: hazCard.code
                                        variant: hazCard.isCritical ? "danger" : "warning"
                                        compact: true
                                    }

                                    Text {
                                        visible: hazCard.taskName.length > 0
                                        Layout.fillWidth: true
                                        text: hazCard.taskName
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textSm
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.textPrimary
                                        elide: Text.ElideRight
                                    }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: hazCard.message
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textSm
                                    color: Theme.textPrimary
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                    }
                }

                // Empty State
                EmptyState {
                    visible: taskRep.count === 0
                    Layout.fillWidth: true
                    Layout.preferredHeight: 240
                    iconName: Icons.layers
                    title: root.rtosObj.error ? qsTr("RTOS Inspection Unavailable") : qsTr("No RTOS Tasks Loaded")
                    description: root.rtosObj.error
                        ? root.rtosObj.error
                        : qsTr("Connect to a target running FreeRTOS / ThreadX / Zephyr with ELF symbols to inspect live tasks and stack high-water marks.")
                }

                // Responsive Task Cards Grid (1-col compact, 2-col wide)
                GridLayout {
                    visible: taskRep.count > 0
                    Layout.fillWidth: true
                    columns: root.compact ? 1 : 2
                    columnSpacing: Theme.s3
                    rowSpacing: Theme.s3

                    Repeater {
                        id: taskRep
                        model: Workbench.rtosTasksModel

                        delegate: Rectangle {
                            id: taskCard
                            required property int index
                            required property var model
                            readonly property string taskName: model.name ?? ""
                            readonly property string tcbAddress: model.tcbAddress ?? ""
                            readonly property string taskState: model.state ?? ""
                            readonly property int priority: model.priority ?? 0
                            readonly property real stackSizeBytes: model.stackSizeBytes ?? 0
                            readonly property real stackFreeMinBytes: model.stackFreeMinBytes ?? 0
                            readonly property real stackUsedPeakPct: model.stackUsedPeakPct ?? 0
                            readonly property bool isCurrent: model.isCurrent ?? false

                            readonly property color stackColor: stackUsedPeakPct >= 85
                                ? Theme.hueRose
                                : (stackUsedPeakPct >= 70 ? Theme.hueAmber : Theme.hueEmerald)

                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            implicitHeight: taskCol.implicitHeight + Theme.s3 * 2
                            radius: Theme.radiusLg
                            color: isCurrent
                                ? Theme.alpha(Theme.hueEmerald, Theme.isDark ? 0.10 : 0.07)
                                : Theme.bgSurface
                            border.color: isCurrent
                                ? Theme.alpha(Theme.hueEmerald, 0.45)
                                : Theme.borderSubtle
                            border.width: 1

                            ColumnLayout {
                                id: taskCol
                                anchors.fill: parent
                                anchors.margins: Theme.s3
                                spacing: Theme.s2

                                // Task name + State + Priority
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    StatusDot {
                                        size: 7
                                        color: taskCard.isCurrent ? Theme.hueEmerald : Theme.textMuted
                                        pulsing: taskCard.isCurrent
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: taskCard.taskName
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textBase
                                        font.weight: taskCard.isCurrent ? Theme.weightSemiBold : Theme.weightMedium
                                        color: Theme.textPrimary
                                        elide: Text.ElideRight
                                    }

                                    Badge {
                                        text: taskCard.taskState || "UNKNOWN"
                                        variant: taskCard.taskState === "RUNNING" ? "success" : (taskCard.taskState === "BLOCKED" ? "warning" : "neutral")
                                        compact: true
                                    }

                                    Badge {
                                        text: "P" + taskCard.priority
                                        variant: "neutral"
                                        compact: true
                                    }
                                }

                                // Stack Watermark Bar
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    AppProgressBar {
                                        Layout.fillWidth: true
                                        barHeight: 7
                                        from: 0
                                        to: 100
                                        value: Math.max(0, Math.min(100, taskCard.stackUsedPeakPct))
                                        fillColor: taskCard.stackColor
                                    }

                                    Text {
                                        Layout.preferredWidth: 48
                                        text: taskCard.stackUsedPeakPct.toFixed(1) + "%"
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textXs
                                        font.weight: Theme.weightSemiBold
                                        color: taskCard.stackColor
                                        horizontalAlignment: Text.AlignRight
                                    }
                                }

                                // Footer metadata: TCB + Free / Size
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    Text {
                                        Layout.fillWidth: true
                                        text: qsTr("TCB %1").arg(taskCard.tcbAddress || "—")
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textXs
                                        color: Theme.textMuted
                                        elide: Text.ElideRight
                                    }

                                    Text {
                                        text: taskCard.stackSizeBytes > 0
                                            ? qsTr("%1 B free / %2 B").arg(Math.round(taskCard.stackFreeMinBytes)).arg(Math.round(taskCard.stackSizeBytes))
                                            : "—"
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textXs
                                        color: Theme.textSecondary
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
