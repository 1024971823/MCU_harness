pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root

    readonly property var rtosObj: Workbench.rtos || ({})
    readonly property bool hasRtos: !!(rtosObj.rtos && rtosObj.rtos.length > 0 && !rtosObj.error)

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.s6
        spacing: Theme.s4

        SectionHeader {
            iconName: Icons.rtos
            title: qsTr("RTOS Kernel & Task Inspector")
            subtitle: qsTr("Task states, scheduling ticks, stack peak watermarks, and automatic hazard detection")

            AppButton {
                buttonSize: "sm"
                variant: "secondary"
                iconName: Icons.refresh
                text: qsTr("Refresh")
                onClicked: Workbench.refreshSnapshot()
            }
        }

        // Summary Chips
        Flow {
            Layout.fillWidth: true
            spacing: Theme.s2

            Badge {
                iconName: Icons.layers
                text: qsTr("Kernel: %1").arg(root.rtosObj.rtos || qsTr("None"))
                variant: root.hasRtos ? "accent" : "neutral"
            }
            Badge {
                iconName: Icons.activity
                text: root.rtosObj.schedulerRunning === true ? qsTr("Scheduler: Running") : (root.rtosObj.schedulerRunning === false ? qsTr("Scheduler: Suspended") : qsTr("Scheduler: —"))
                variant: root.rtosObj.schedulerRunning === true ? "success" : "neutral"
            }
            Badge {
                iconName: Icons.timer
                text: qsTr("Tick: %1").arg(root.rtosObj.tickCount !== undefined && root.rtosObj.tickCount !== null ? root.rtosObj.tickCount : "—")
                variant: "neutral"
            }
            Badge {
                iconName: Icons.circleDot
                text: qsTr("Current: %1").arg(root.rtosObj.currentTask || "—")
                variant: root.rtosObj.currentTask ? "info" : "neutral"
            }
            Badge {
                iconName: Icons.listChecks
                text: qsTr("Tasks: %1").arg(taskList.count)
                variant: "neutral"
            }
        }

        // Hazards Callout Stack
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

                    Layout.fillWidth: true
                    implicitHeight: hazRow.implicitHeight + Theme.s3 * 2
                    radius: Theme.radiusMd
                    color: isCritical ? Theme.dangerSubtle : Theme.warningSubtle
                    border.color: isCritical ? Theme.danger : Theme.warning
                    border.width: 1

                    RowLayout {
                        id: hazRow
                        anchors.fill: parent
                        anchors.margins: Theme.s3
                        spacing: Theme.s3

                        Icon {
                            name: Icons.triangleAlert
                            size: 16
                            color: hazCard.isCritical ? Theme.danger : Theme.warning
                        }

                        Badge {
                            text: hazCard.code
                            variant: hazCard.isCritical ? "danger" : "warning"
                            compact: true
                        }

                        Text {
                            visible: hazCard.taskName.length > 0
                            text: hazCard.taskName
                            font.family: Theme.fontMono
                            font.pixelSize: Theme.textSm
                            font.weight: Theme.weightSemiBold
                            color: Theme.textPrimary
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

        // Task Table Card
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusLg
            color: Theme.bgSurface
            border.color: Theme.borderSubtle
            border.width: 1

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 34
                    color: Theme.bgElevated
                    topLeftRadius: Theme.radiusLg
                    topRightRadius: Theme.radiusLg

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.s4
                        anchors.rightMargin: Theme.s4
                        spacing: Theme.s3

                        Text { Layout.preferredWidth: 170; text: qsTr("TASK");       font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 100; text: qsTr("STATE");      font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 64;  text: qsTr("PRIO");       font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 110; text: qsTr("TCB");        font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.fillWidth: true;     text: qsTr("STACK PEAK"); font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 130; text: qsTr("FREE / SIZE");font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted; horizontalAlignment: Text.AlignRight }
                    }
                }

                Divider { Layout.fillWidth: true }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    EmptyState {
                        visible: taskList.count === 0
                        anchors.fill: parent
                        iconName: Icons.rtos
                        title: root.rtosObj.error ? qsTr("RTOS Inspection Unavailable") : qsTr("No RTOS Tasks Loaded")
                        description: root.rtosObj.error
                            ? root.rtosObj.error
                            : qsTr("Connect to a target running FreeRTOS / ThreadX / Zephyr with ELF symbols to inspect live tasks and stack high-water marks.")
                    }

                    ListView {
                        id: taskList
                        visible: count > 0
                        anchors.fill: parent
                        clip: true
                        model: Workbench.rtosTasksModel
                        ScrollBar.vertical: AppScrollBar {}

                        delegate: Rectangle {
                            id: taskRow
                            required property int index
                            required property var model
                            readonly property string taskName: model.name ?? ""
                            readonly property string tcbAddress: model.tcbAddress ?? ""
                            readonly property string taskState: model.state ?? ""
                            readonly property int priority: model.priority ?? 0
                            readonly property int stackSizeBytes: model.stackSizeBytes ?? 0
                            readonly property int stackFreeMinBytes: model.stackFreeMinBytes ?? 0
                            readonly property real stackUsedPeakPct: model.stackUsedPeakPct ?? 0
                            readonly property bool isCurrent: model.isCurrent ?? false

                            readonly property color stackColor: stackUsedPeakPct > 85 ? Theme.danger : (stackUsedPeakPct >= 70 ? Theme.warning : Theme.success)

                            width: taskList.width
                            height: 40
                            color: isCurrent ? Theme.accentSubtle : (index % 2 === 1 ? Qt.rgba(Theme.bgElevated.r, Theme.bgElevated.g, Theme.bgElevated.b, 0.35) : "transparent")

                            Divider {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                height: 1
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: Theme.s4
                                anchors.rightMargin: Theme.s4
                                spacing: Theme.s3

                                RowLayout {
                                    Layout.preferredWidth: 170
                                    spacing: Theme.s1 + 2

                                    StatusDot {
                                        size: 7
                                        color: taskRow.isCurrent ? Theme.success : Theme.textMuted
                                        pulsing: taskRow.isCurrent
                                    }

                                    Text {
                                        Layout.fillWidth: true
                                        text: taskRow.taskName
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textSm
                                        font.weight: taskRow.isCurrent ? Theme.weightSemiBold : Theme.weightMedium
                                        color: Theme.textPrimary
                                        elide: Text.ElideRight
                                    }
                                }

                                Item {
                                    Layout.preferredWidth: 100
                                    Layout.preferredHeight: 24
                                    Badge {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: taskRow.taskState || "UNKNOWN"
                                        variant: taskRow.taskState === "RUNNING" ? "success" : (taskRow.taskState === "BLOCKED" ? "warning" : "neutral")
                                        compact: true
                                    }
                                }

                                Text {
                                    Layout.preferredWidth: 64
                                    text: String(taskRow.priority)
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textSm
                                    color: Theme.textSecondary
                                }

                                Text {
                                    Layout.preferredWidth: 110
                                    text: taskRow.tcbAddress || "—"
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                }

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    AppProgressBar {
                                        Layout.fillWidth: true
                                        from: 0
                                        to: 100
                                        value: Math.max(0, Math.min(100, taskRow.stackUsedPeakPct))
                                        fillColor: taskRow.stackColor
                                    }

                                    Text {
                                        Layout.preferredWidth: 46
                                        text: taskRow.stackUsedPeakPct.toFixed(1) + "%"
                                        font.family: Theme.fontMono
                                        font.pixelSize: Theme.textXs
                                        font.weight: Theme.weightSemiBold
                                        color: taskRow.stackColor
                                        horizontalAlignment: Text.AlignRight
                                    }
                                }

                                Text {
                                    Layout.preferredWidth: 130
                                    text: taskRow.stackSizeBytes > 0
                                        ? (taskRow.stackFreeMinBytes + "B / " + taskRow.stackSizeBytes + "B")
                                        : "—"
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textSecondary
                                    horizontalAlignment: Text.AlignRight
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
