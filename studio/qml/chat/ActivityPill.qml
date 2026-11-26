pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root

    signal reviewApprovalRequested()

    property real _nowMs: Date.now()
    readonly property string phase: Workbench.agentPhase || "idle"
    readonly property bool isApproval: phase === "approval"

    readonly property string phaseText: {
        switch (phase) {
        case "waiting":
            return qsTr("Connecting to model…")
        case "thinking":
            return qsTr("Thinking…")
        case "responding":
            return qsTr("Writing response…")
        case "tool":
            return qsTr("Running %1…").arg(Workbench.currentToolTitle || qsTr("tool"))
        case "approval":
            return qsTr("Waiting for your approval")
        default:
            return qsTr("Working…")
        }
    }

    readonly property string elapsedText: {
        var started = Workbench.turnStartedMs || 0
        var ms = started > 0 ? Math.max(0, _nowMs - started) : 0
        return (ms / 1000.0).toFixed(1) + "s"
    }

    visible: Workbench.turnRunning
    implicitWidth: contentRow.implicitWidth + Theme.s3 * 2 + 2
    implicitHeight: visible ? 34 : 0

    Component.onCompleted: _nowMs = Date.now()

    Connections {
        target: Workbench
        function onAgentPhaseChanged() {
            root._nowMs = Date.now()
        }
    }

    Timer {
        interval: 100
        repeat: true
        running: Workbench.turnRunning && root.visible && !AppInfo.screenshotMode
        onTriggered: root._nowMs = Date.now()
    }

    // Outer gradient border pill
    Rectangle {
        id: outerPill
        anchors.centerIn: parent
        width: root.width > 0 ? Math.min(root.width, root.implicitWidth) : root.implicitWidth
        height: 34
        radius: Theme.radiusPill
        color: root.isApproval ? Theme.hueAmber : "transparent"

        gradient: root.isApproval ? null : brandGrad

        Gradient {
            id: brandGrad
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: Theme.brandA }
            GradientStop { position: 0.5; color: Theme.brandB }
            GradientStop { position: 1.0; color: Theme.brandC }
        }

        Rectangle {
            id: innerPill
            anchors.fill: parent
            anchors.margins: 1
            radius: Theme.radiusPill
            color: root.isApproval ? Theme.bgSurface : Theme.bgElevated

            RowLayout {
                id: contentRow
                anchors.centerIn: parent
                spacing: Theme.s2

                AgentOrb {
                    visible: !root.isApproval
                    size: 20
                    spinning: Workbench.turnRunning && !root.isApproval
                    icon: Icons.sparkles
                }

                Icon {
                    visible: root.isApproval
                    name: Icons.shieldAlert
                    size: 15
                    color: Theme.hueAmber
                }

                Text {
                    text: root.phaseText
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textSm
                    font.weight: Theme.weightMedium
                    color: root.isApproval ? Theme.hueAmber : Theme.textPrimary
                    elide: Text.ElideRight
                    Layout.maximumWidth: 320
                }

                Text {
                    text: root.elapsedText
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.textXs
                    color: root.isApproval ? Theme.hueAmber : Theme.textSecondary
                }

                Rectangle {
                    visible: (Workbench.currentStep || 0) > 0
                    implicitWidth: stepText.implicitWidth + Theme.s2
                    implicitHeight: 18
                    radius: Theme.radiusPill
                    color: Theme.alpha(Theme.accent, Theme.isDark ? 0.16 : 0.10)

                    Text {
                        id: stepText
                        anchors.centerIn: parent
                        text: qsTr("Step %1").arg(Workbench.currentStep || 0)
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.textXs
                        font.weight: Theme.weightSemiBold
                        color: Theme.accent
                    }
                }

                Pill {
                    visible: root.isApproval
                    compact: true
                    clickable: true
                    hue: Theme.hueAmber
                    icon: Icons.shieldCheck
                    text: qsTr("Review")
                    onClicked: root.reviewApprovalRequested()
                }

                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: 14
                    color: Theme.border
                }

                Rectangle {
                    id: stopBtn
                    implicitWidth: stopRow.implicitWidth + Theme.s2 + 2
                    implicitHeight: 22
                    radius: Theme.radiusPill
                    color: stopHover.hovered ? Theme.alpha(Theme.hueRose, 0.22) : Theme.alpha(Theme.hueRose, 0.12)
                    border.color: Theme.alpha(Theme.hueRose, stopHover.hovered ? 0.55 : 0.30)
                    border.width: 1

                    HoverHandler {
                        id: stopHover
                        cursorShape: Qt.PointingHandCursor
                    }
                    TapHandler {
                        onTapped: Workbench.abortTurn()
                    }

                    RowLayout {
                        id: stopRow
                        anchors.centerIn: parent
                        spacing: Theme.s1

                        Icon {
                            name: Icons.square
                            size: 10
                            color: Theme.hueRose
                        }

                        Text {
                            text: qsTr("Stop")
                            font.family: Theme.fontSans
                            font.pixelSize: Theme.textXs
                            font.weight: Theme.weightSemiBold
                            color: Theme.hueRose
                        }

                        Text {
                            text: "Esc"
                            font.family: Theme.fontMono
                            font.pixelSize: 10
                            color: Theme.textMuted
                        }
                    }
                }
            }
        }
    }
}
