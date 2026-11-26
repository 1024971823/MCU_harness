pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root

    function submitWatch() {
        var expr = exprField.text.trim()
        if (expr.length === 0) return
        Workbench.addWatch(expr)
        exprField.text = ""
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.s6
        spacing: Theme.s4

        SectionHeader {
            iconName: Icons.watch
            title: qsTr("DWARF Live Watch")
            subtitle: qsTr("Read global symbols, struct members, and pointers in real time without halting the CPU")

            AppButton {
                buttonSize: "sm"
                variant: "secondary"
                iconName: Icons.refresh
                text: qsTr("Refresh")
                onClicked: Workbench.refreshSnapshot()
            }

            AppButton {
                buttonSize: "sm"
                variant: "ghost"
                iconName: Icons.trash
                text: qsTr("Clear All")
                enabled: watchList.count > 0
                onClicked: Workbench.clearWatch()
            }
        }

        // Add Expression Bar
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            AppTextField {
                id: exprField
                Layout.fillWidth: true
                monospace: true
                leadingIcon: Icons.plus
                placeholderText: qsTr("Enter C expression or symbol (e.g. g_ticks, motor.iq_meas, TIM1->CCR1)...")
                clearable: true
                onAccepted: root.submitWatch()
            }

            AppButton {
                variant: "primary"
                iconName: Icons.plus
                text: qsTr("Add Watch")
                enabled: exprField.text.trim().length > 0
                onClicked: root.submitWatch()
            }
        }

        // Table Card
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

                // Table Header
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

                        Text { Layout.preferredWidth: 200; text: qsTr("EXPRESSION"); font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 140; text: qsTr("TYPE");       font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 110; text: qsTr("ADDRESS");    font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.fillWidth: true;     text: qsTr("VALUE");      font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Text { Layout.preferredWidth: 120; text: qsTr("HEX");        font.family: Theme.fontSans; font.pixelSize: Theme.textXs; font.weight: Theme.weightSemiBold; color: Theme.textMuted }
                        Item { Layout.preferredWidth: 56 }
                    }
                }

                Divider { Layout.fillWidth: true }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    EmptyState {
                        visible: watchList.count === 0
                        anchors.fill: parent
                        iconName: Icons.watch
                        title: qsTr("No Watch Expressions")
                        description: qsTr("Add a firmware symbol or struct field above to monitor live memory values via ELF DWARF info.")
                    }

                    ListView {
                        id: watchList
                        visible: count > 0
                        anchors.fill: parent
                        clip: true
                        model: Workbench.watchModel
                        ScrollBar.vertical: AppScrollBar {}

                        delegate: Rectangle {
                            id: watchRow
                            required property int index
                            required property string expr
                            required property string addressHex
                            required property string typeName
                            required property string display
                            required property string hex
                            required property string error
                            required property int changeSerial

                            readonly property bool hasError: error && error.length > 0

                            width: watchList.width
                            height: 36
                            color: hasError ? Theme.warningSubtle : (index % 2 === 1 ? Qt.rgba(Theme.bgElevated.r, Theme.bgElevated.g, Theme.bgElevated.b, 0.35) : "transparent")

                            // Value change flash overlay (600ms)
                            Rectangle {
                                id: flashRect
                                anchors.fill: parent
                                color: Theme.accentSubtle
                                opacity: 0.0
                            }

                            onChangeSerialChanged: {
                                if (changeSerial > 0) flashAnim.restart()
                            }

                            SequentialAnimation {
                                id: flashAnim
                                NumberAnimation { target: flashRect; property: "opacity"; from: 0.0; to: 1.0; duration: 90 }
                                NumberAnimation { target: flashRect; property: "opacity"; from: 1.0; to: 0.0; duration: 510; easing.type: Easing.OutCubic }
                            }

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
                                    Layout.preferredWidth: 200
                                    text: watchRow.expr
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textSm
                                    font.weight: Theme.weightMedium
                                    color: Theme.textPrimary
                                    elide: Text.ElideRight
                                }

                                Text {
                                    Layout.preferredWidth: 140
                                    text: watchRow.typeName || "—"
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textSecondary
                                    elide: Text.ElideRight
                                }

                                Text {
                                    Layout.preferredWidth: 110
                                    text: watchRow.addressHex || "—"
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: watchRow.hasError ? watchRow.error : watchRow.display
                                    font.family: watchRow.hasError ? Theme.fontSans : Theme.fontMono
                                    font.pixelSize: Theme.textSm
                                    font.weight: watchRow.hasError ? Theme.weightNormal : Theme.weightSemiBold
                                    color: watchRow.hasError ? Theme.warning : Theme.textPrimary
                                    elide: Text.ElideRight
                                }

                                Text {
                                    Layout.preferredWidth: 120
                                    text: watchRow.hex || "—"
                                    font.family: Theme.fontMono
                                    font.pixelSize: Theme.textXs
                                    color: Theme.textSecondary
                                    elide: Text.ElideRight
                                }

                                RowLayout {
                                    Layout.preferredWidth: 56
                                    spacing: Theme.s1

                                    IconButton {
                                        buttonSize: 24
                                        iconSize: 13
                                        iconName: Icons.copy
                                        tooltipText: qsTr("Copy value")
                                        onClicked: Utils.copyToClipboard(watchRow.hasError ? watchRow.error : watchRow.display)
                                    }

                                    IconButton {
                                        buttonSize: 24
                                        iconSize: 13
                                        iconName: Icons.trash
                                        danger: true
                                        tooltipText: qsTr("Remove watch")
                                        onClicked: Workbench.removeWatch(watchRow.expr)
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
