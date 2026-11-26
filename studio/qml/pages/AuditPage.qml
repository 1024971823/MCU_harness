pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root

    property string searchText: ""
    property string verdictFilter: "all" // "all" | "allowed" | "blocked"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.s6
        spacing: Theme.s4

        SectionHeader {
            iconName: Icons.audit
            title: qsTr("Safety Audit Log")
            subtitle: qsTr("Hardware safety guard decisions, HITL approvals, and memory write-protection blocks")

            AppButton {
                buttonSize: "sm"
                variant: "secondary"
                iconName: Icons.refresh
                text: qsTr("Refresh")
                onClicked: Workbench.refreshSnapshot()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s3

            AppTextField {
                Layout.fillWidth: true
                leadingIcon: Icons.search
                placeholderText: qsTr("Filter audit log by operation, target, or detail...")
                clearable: true
                text: root.searchText
                onTextEdited: root.searchText = text
            }

            SegmentedControl {
                currentValue: root.verdictFilter
                items: [
                    { value: "all",     label: qsTr("All") },
                    { value: "allowed", label: qsTr("Allowed") },
                    { value: "blocked", label: qsTr("Blocked") }
                ]
                onActivated: function(val) { root.verdictFilter = val }
            }
        }

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

                        Text { Layout.preferredWidth: 84;  text: qsTr("TIME");      font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 82;  text: qsTr("VERDICT");   font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 126; text: qsTr("RISK LEVEL");font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 140; text: qsTr("OPERATION"); font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 160; text: qsTr("TARGET");    font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.fillWidth: true;     text: qsTr("DETAIL");    font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                    }
                }

                Divider { Layout.fillWidth: true }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    EmptyState {
                        visible: auditList.count === 0
                        anchors.fill: parent
                        iconName: Icons.audit
                        title: qsTr("No Safety Audit Events")
                        description: qsTr("Probe actions, register writes, and HITL interlock decisions will be recorded here automatically.")
                    }

                    ListView {
                        id: auditList
                        visible: count > 0
                        anchors.fill: parent
                        clip: true
                        model: Workbench.auditModel
                        ScrollBar.vertical: AppScrollBar {}

                        delegate: Rectangle {
                            id: auditRow
                            required property int index
                            required property string timeText
                            required property string operation
                            required property string target
                            required property string level
                            required property bool allowed
                            required property string detail

                            readonly property bool matchesFilter: {
                                if (root.verdictFilter === "allowed" && !allowed) return false
                                if (root.verdictFilter === "blocked" && allowed) return false
                                var q = root.searchText.trim().toLowerCase()
                                if (q.length === 0) return true
                                return operation.toLowerCase().indexOf(q) !== -1
                                    || target.toLowerCase().indexOf(q) !== -1
                                    || detail.toLowerCase().indexOf(q) !== -1
                                    || level.toLowerCase().indexOf(q) !== -1
                            }

                            visible: matchesFilter
                            width: auditList.width
                            height: visible ? 36 : 0
                            color: index % 2 === 1 ? Qt.rgba(Theme.bgElevated.r, Theme.bgElevated.g, Theme.bgElevated.b, 0.35) : "transparent"

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

                                Text {
                                    Layout.preferredWidth: 84
                                    text: auditRow.timeText
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textMuted
                                }

                                Item {
                                    Layout.preferredWidth: 82
                                    Layout.preferredHeight: 24
                                    Badge {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: auditRow.allowed ? qsTr("ALLOW") : qsTr("BLOCK")
                                        variant: auditRow.allowed ? "success" : "danger"
                                        compact: true
                                    }
                                }

                                Item {
                                    Layout.preferredWidth: 126
                                    Layout.preferredHeight: 24
                                    Badge {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: auditRow.level.toUpperCase()
                                        variant: auditRow.level === "hardware_danger" ? "danger"
                                            : (auditRow.level === "hardware_state" ? "warning"
                                            : (auditRow.level === "workspace_write" ? "info"
                                            : (auditRow.level === "shell" ? "purple" : "success")))
                                        compact: true
                                    }
                                }

                                Text {
                                    Layout.preferredWidth: 140
                                    text: auditRow.operation
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textSm
                                    font.weight: Theme.weightMedium
                                    color: Theme.textPrimary
                                    elide: Text.ElideRight
                                }

                                Text {
                                    Layout.preferredWidth: 160
                                    text: auditRow.target
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textSm
                                    color: Theme.textSecondary
                                    elide: Text.ElideRight
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: auditRow.detail || "—"
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textSm
                                    color: Theme.textSecondary
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
