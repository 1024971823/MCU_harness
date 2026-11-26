pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root
    property bool compact: width < 560
    signal toastRequested(string msg, string level)

    property string searchText: ""
    property string verdictFilter: "all" // "all" | "allowed" | "blocked"
    property string scopeFilter: "all"   // "all" | "hardware" | "workspace"

    FileDialog {
        id: diagExportDlg
        title: qsTr("Export Sanitized Diagnostics Bundle")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("Zip archives (*.zip)")]
        onAccepted: {
            Diagnostics.exportBundle(selectedFile)
            root.toastRequested(qsTr("Diagnostics bundle exported"), "success")
        }
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
                icon: Icons.shieldCheck
                hue: Theme.hueRose
                size: 32
                iconSize: 16
            }

            ColumnLayout {
                visible: !root.compact
                Layout.fillWidth: true
                spacing: 1

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Safety Audit")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textLg
                    font.weight: Theme.weightSemiBold
                    color: Theme.textPrimary
                    elide: Text.ElideRight
                }

                Text {
                    Layout.fillWidth: true
                    text: qsTr("HITL interlock decisions & write-protection trail")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textXs
                    color: Theme.textSecondary
                    elide: Text.ElideRight
                }
            }

            Text {
                visible: root.compact
                Layout.fillWidth: true
                text: qsTr("HITL interlock decisions & write-protection trail")
                font.family: Theme.fontSans
                font.pixelSize: Theme.textXs
                color: Theme.textSecondary
                elide: Text.ElideRight
            }

            IconButton {
                buttonSize: 28
                iconSize: 14
                iconName: Icons.refresh
                tooltipText: qsTr("Refresh Audit Log")
                onClicked: Workbench.refreshSnapshot()
            }

            AppButton {
                buttonSize: "sm"
                variant: "secondary"
                iconName: Icons.fileDown
                text: root.compact ? qsTr("Export") : qsTr("Export ZIP...")
                tooltipText: qsTr("Export sanitized diagnostics bundle (.zip)")
                onClicked: diagExportDlg.open()
            }
        }

        // Search & Filter Chips
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            SearchField {
                Layout.fillWidth: true
                placeholderText: qsTr("Filter by operation, target, risk, or detail...")
                text: root.searchText
                onTextEdited: root.searchText = text
                onCleared: root.searchText = ""
            }

            Flow {
                Layout.fillWidth: true
                spacing: Theme.s1 + 2

                Chip {
                    text: qsTr("All")
                    hue: Theme.hueRose
                    checkable: false
                    active: root.verdictFilter === "all"
                    onClicked: root.verdictFilter = "all"
                }
                Chip {
                    text: qsTr("Allowed")
                    icon: Icons.circleCheck
                    hue: Theme.hueEmerald
                    checkable: false
                    active: root.verdictFilter === "allowed"
                    onClicked: root.verdictFilter = "allowed"
                }
                Chip {
                    text: qsTr("Blocked")
                    icon: Icons.shieldOff
                    hue: Theme.hueRose
                    checkable: false
                    active: root.verdictFilter === "blocked"
                    onClicked: root.verdictFilter = "blocked"
                }

                Chip {
                    text: qsTr("Hardware")
                    icon: Icons.cpu
                    hue: Theme.hueAmber
                    checkable: false
                    active: root.scopeFilter === "hardware"
                    onClicked: root.scopeFilter = (root.scopeFilter === "hardware" ? "all" : "hardware")
                }
                Chip {
                    text: qsTr("Workspace")
                    icon: Icons.fileCode
                    hue: Theme.hueViolet
                    checkable: false
                    active: root.scopeFilter === "workspace"
                    onClicked: root.scopeFilter = (root.scopeFilter === "workspace" ? "all" : "workspace")
                }
            }
        }

        // Event List
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            EmptyState {
                visible: auditList.count === 0
                anchors.fill: parent
                iconName: Icons.shieldCheck
                title: qsTr("No Safety Audit Events")
                description: qsTr("Probe actions, register writes, and HITL interlock decisions will be recorded here automatically.")
            }

            ListView {
                id: auditList
                visible: count > 0
                anchors.fill: parent
                spacing: Theme.s2
                clip: true
                model: Workbench.auditModel
                ScrollBar.vertical: AppScrollBar {}

                delegate: Rectangle {
                    id: auditCard
                    required property int index
                    required property string timeText
                    required property string operation
                    required property string target
                    required property string level
                    required property bool allowed
                    required property string detail

                    readonly property bool isHardwareEvent: level === "hardware_danger" || level === "hardware_state"
                    readonly property color riskColor: PluginRegistry.riskHue(level)
                    readonly property bool matchesFilter: {
                        if (root.verdictFilter === "allowed" && !allowed) return false
                        if (root.verdictFilter === "blocked" && allowed) return false
                        if (root.scopeFilter === "hardware" && !isHardwareEvent) return false
                        if (root.scopeFilter === "workspace" && isHardwareEvent) return false
                        var q = root.searchText.trim().toLowerCase()
                        if (q.length === 0) return true
                        return operation.toLowerCase().indexOf(q) !== -1
                            || target.toLowerCase().indexOf(q) !== -1
                            || detail.toLowerCase().indexOf(q) !== -1
                            || level.toLowerCase().indexOf(q) !== -1
                    }

                    visible: matchesFilter
                    width: auditList.width
                    height: visible ? cardCol.implicitHeight + Theme.s3 * 2 : 0
                    radius: Theme.radiusLg
                    color: !allowed ? Theme.alpha(Theme.hueRose, Theme.isDark ? 0.08 : 0.05) : Theme.bgSurface
                    border.color: !allowed ? Theme.alpha(Theme.hueRose, 0.45) : Theme.borderSubtle
                    border.width: 1

                    ColumnLayout {
                        id: cardCol
                        anchors.fill: parent
                        anchors.margins: Theme.s3
                        spacing: Theme.s1 + 2

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.s2

                            Badge {
                                text: auditCard.allowed ? qsTr("ALLOW") : qsTr("BLOCK")
                                variant: auditCard.allowed ? "success" : "danger"
                                compact: true
                            }

                            Pill {
                                hue: auditCard.riskColor
                                text: auditCard.level.toUpperCase()
                                compact: true
                            }

                            Badge {
                                text: auditCard.isHardwareEvent ? qsTr("HW") : qsTr("WS")
                                variant: auditCard.isHardwareEvent ? "warning" : "purple"
                                compact: true
                            }

                            Item { Layout.fillWidth: true }

                            Text {
                                text: auditCard.timeText
                                font.family: Theme.fontMono
                                font.pixelSize: Theme.textXs
                                color: Theme.textMuted
                            }
                        }

                        // Operation & Target
                        GridLayout {
                            Layout.fillWidth: true
                            columns: root.compact ? 1 : 2
                            columnSpacing: Theme.s3
                            rowSpacing: 2

                            Text {
                                Layout.fillWidth: true
                                Layout.preferredWidth: root.compact ? -1 : 1
                                text: auditCard.operation
                                font.family: Theme.fontMono
                                font.pixelSize: Theme.textSm
                                font.weight: Theme.weightSemiBold
                                color: Theme.textPrimary
                                elide: Text.ElideRight
                            }

                            Text {
                                Layout.fillWidth: true
                                Layout.preferredWidth: root.compact ? -1 : 2
                                text: auditCard.target || "—"
                                font.family: Theme.fontMono
                                font.pixelSize: Theme.textXs
                                color: Theme.textSecondary
                                elide: Text.ElideMiddle
                            }
                        }

                        Text {
                            visible: (auditCard.detail || "").length > 0
                            Layout.fillWidth: true
                            text: auditCard.detail || ""
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textSm
                            color: auditCard.allowed ? Theme.textSecondary : Theme.textPrimary
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }
        }
    }
}
