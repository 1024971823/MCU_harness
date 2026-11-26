pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root

    signal skillClicked(string cmd)

    readonly property var skillCards: [
        {
            cmd: "/crash",
            title: qsTr("Diagnose HardFault"),
            desc: qsTr("Decode CFSR/HFSR, stacked PC/LR & RTOS tasks")
        },
        {
            cmd: "/doctor",
            title: qsTr("Peripheral Doctor"),
            desc: qsTr("Verify RCC clocks, NVIC, GPIO AF & live SVD diff")
        },
        {
            cmd: "/bringup",
            title: qsTr("Board Bring-Up"),
            desc: qsTr("Inspect .ioc wiring, build ELF & check boot logs")
        },
        {
            cmd: "/refactor",
            title: qsTr("Architecture Refactor"),
            desc: qsTr("Audit HAL/BSP boundaries, ISR safety & footprint")
        }
    ]

    AuroraBackdrop {
        anchors.fill: parent
        animated: Theme.animationsEnabled
    }

    Flickable {
        id: emptyFlick
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: Math.max(height, contentCol.implicitHeight + Theme.s8 * 2)
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: AppScrollBar {}

        ColumnLayout {
            id: contentCol
            x: Math.max(Theme.s6, (emptyFlick.width - width) / 2)
            y: Math.max(Theme.s6, (emptyFlick.height - implicitHeight) / 2 - Theme.s2)
            width: Math.min(emptyFlick.width - Theme.s6 * 2, Theme.chatMaxWidth)
            height: implicitHeight
            spacing: Theme.s6

            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignHCenter
                spacing: Theme.s3

                AgentOrb {
                    Layout.alignment: Qt.AlignHCenter
                    size: 64
                    animated: Theme.animationsEnabled
                    icon: Icons.sparkles
                }

                Text {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.fillWidth: true
                    text: qsTr("What are we debugging today?")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.text2xl
                    font.weight: Theme.weightSemiBold
                    color: Theme.textPrimary
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                Text {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.fillWidth: true
                    Layout.maximumWidth: 580
                    text: qsTr("Closed-loop Cortex-M debugging with CMSIS-SVD register decode, non-blocking RTT/UART logs, and hardware safety interlocks.")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textMd
                    color: Theme.textSecondary
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }
            }

            GridLayout {
                Layout.fillWidth: true
                columns: contentCol.width < 560 ? 1 : 2
                columnSpacing: Theme.s3
                rowSpacing: Theme.s3

                Repeater {
                    model: root.skillCards

                    delegate: Rectangle {
                        id: card
                        required property var modelData
                        readonly property var meta: PluginRegistry.skillMeta(modelData.cmd)
                        readonly property color hue: meta ? meta.hue : Theme.hueIndigo
                        readonly property string iconName: meta ? meta.icon : Icons.sparkles

                        Layout.fillWidth: true
                        implicitHeight: cardRow.implicitHeight + Theme.s4 * 2
                        radius: Theme.radiusXl
                        color: cardHover.hovered ? Theme.bgElevated : Theme.bgSurface
                        border.color: cardHover.hovered ? Theme.alpha(card.hue, 0.72) : Theme.borderSubtle
                        border.width: 1
                        activeFocusOnTab: true

                        transform: Translate {
                            y: cardHover.hovered ? -2 : 0
                            Behavior on y {
                                NumberAnimation {
                                    duration: Theme.animationsEnabled ? Theme.animFast : 0
                                    easing.type: Theme.easingType
                                }
                            }
                        }

                        Behavior on color {
                            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                        }
                        Behavior on border.color {
                            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                        }

                        Keys.onReturnPressed: root.skillClicked(card.modelData.cmd)
                        Keys.onSpacePressed: root.skillClicked(card.modelData.cmd)

                        HoverHandler {
                            id: cardHover
                            cursorShape: Qt.PointingHandCursor
                        }

                        TapHandler {
                            onTapped: root.skillClicked(card.modelData.cmd)
                        }

                        Rectangle {
                            anchors.fill: parent
                            anchors.margins: -3
                            radius: Theme.radiusXl + 2
                            color: "transparent"
                            border.color: Theme.accent
                            border.width: Theme.focusRingWidth
                            visible: card.activeFocus
                        }

                        RowLayout {
                            id: cardRow
                            anchors.fill: parent
                            anchors.margins: Theme.s4
                            spacing: Theme.s3

                            IconChip {
                                Layout.alignment: Qt.AlignTop
                                size: 38
                                icon: card.iconName
                                hue: card.hue
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s1

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.s2

                                    Text {
                                        Layout.fillWidth: true
                                        text: card.modelData.title
                                        font.family: Theme.fontSans
                                        font.pixelSize: Theme.textBase
                                        font.weight: Theme.weightSemiBold
                                        color: Theme.textPrimary
                                        elide: Text.ElideRight
                                    }

                                    Rectangle {
                                        implicitWidth: cmdText.implicitWidth + Theme.s2 + 2
                                        implicitHeight: 20
                                        radius: Theme.radiusSm + 2
                                        color: Theme.alpha(card.hue, Theme.isDark ? 0.14 : 0.10)
                                        border.color: Theme.alpha(card.hue, 0.30)
                                        border.width: 1

                                        Text {
                                            id: cmdText
                                            anchors.centerIn: parent
                                            text: card.modelData.cmd
                                            font.family: Theme.fontMono
                                            font.pixelSize: Theme.textXs
                                            font.weight: Theme.weightSemiBold
                                            color: card.hue
                                        }
                                    }
                                }

                                Text {
                                    Layout.fillWidth: true
                                    text: card.modelData.desc
                                    font.family: Theme.fontSans
                                    font.pixelSize: Theme.textSm
                                    color: Theme.textSecondary
                                    elide: Text.ElideRight
                                    maximumLineCount: 1
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
