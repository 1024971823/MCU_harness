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

    function submitWatch() {
        var expr = exprField.text.trim()
        if (expr.length === 0) return
        Workbench.addWatch(expr)
        exprField.text = ""
        root.toastRequested(qsTr("Added watch: %1").arg(expr), "info")
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
                icon: Icons.variable
                hue: Theme.hueCyan
                size: 32
                iconSize: 16
            }

            ColumnLayout {
                visible: !root.compact
                Layout.fillWidth: true
                spacing: 1

                Text {
                    Layout.fillWidth: true
                    text: qsTr("DWARF Live Watch")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textLg
                    font.weight: Theme.weightSemiBold
                    color: Theme.textPrimary
                    elide: Text.ElideRight
                }

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Non-intrusive RAM symbol & expression sampling")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textXs
                    color: Theme.textSecondary
                    elide: Text.ElideRight
                }
            }

            Text {
                visible: root.compact
                Layout.fillWidth: true
                text: qsTr("Non-intrusive RAM symbol & expression sampling")
                font.family: Theme.fontSans
                font.pixelSize: Theme.textXs
                color: Theme.textSecondary
                elide: Text.ElideRight
            }

            Pill {
                hue: Theme.hueCyan
                text: qsTr("%1 symbols").arg(watchRep.count)
                compact: root.compact
            }

            IconButton {
                buttonSize: 28
                iconSize: 14
                iconName: Icons.refresh
                tooltipText: qsTr("Refresh Watch Values")
                onClicked: {
                    Workbench.refreshSnapshot()
                    root.toastRequested(qsTr("Watch values refreshed"), "info")
                }
            }

            IconButton {
                buttonSize: 28
                iconSize: 14
                iconName: Icons.trash
                danger: true
                enabled: watchRep.count > 0
                tooltipText: qsTr("Clear All Watches")
                onClicked: {
                    Workbench.clearWatch()
                    root.toastRequested(qsTr("Cleared all watches"), "info")
                }
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
                placeholderText: root.compact
                    ? qsTr("Symbol or expression (e.g. g_ticks)...")
                    : qsTr("Enter C expression or symbol (e.g. g_ticks, motor.iq_meas, TIM1->CCR1)...")
                clearable: true
                onAccepted: root.submitWatch()
            }

            AppButton {
                variant: "primary"
                iconName: Icons.plus
                text: root.compact ? qsTr("Add") : qsTr("Add Watch")
                enabled: exprField.text.trim().length > 0
                onClicked: root.submitWatch()
            }
        }

        // Watch Cards Container (1-col in compact panel, 2-col grid when maximized)
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            EmptyState {
                visible: watchRep.count === 0
                anchors.fill: parent
                iconName: Icons.variable
                title: qsTr("No Watch Expressions")
                description: qsTr("Add a firmware symbol or struct field above to monitor live memory values via ELF DWARF info.")
            }

            ScrollView {
                id: watchScroll
                visible: watchRep.count > 0
                anchors.fill: parent
                clip: true
                ScrollBar.vertical: AppScrollBar {}
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                Item {
                    width: watchScroll.availableWidth
                    implicitHeight: watchGrid.implicitHeight + Theme.s2

                    GridLayout {
                        id: watchGrid
                        anchors.top: parent.top
                        anchors.left: parent.left
                        anchors.right: parent.right
                        columns: root.compact ? 1 : 2
                        columnSpacing: Theme.s3
                        rowSpacing: Theme.s3

                        Repeater {
                            id: watchRep
                            model: Workbench.watchModel

                            delegate: Rectangle {
                                id: watchCard
                                required property int index
                                required property string expr
                                required property string addressHex
                                required property string typeName
                                required property string kind
                                required property string display
                                required property string hex
                                required property string error
                                required property bool changed
                                required property int changeSerial
                                required property var history

                                readonly property bool hasError: error && error.length > 0
                                property bool showDetails: false
                                readonly property var samples: {
                                    if (history && history.length > 0) return history
                                    var v = parseFloat(display)
                                    return (!hasError && !isNaN(v) && isFinite(v)) ? [v] : []
                                }

                                Component.onCompleted: sparkCanvas.requestPaint()
                                onHistoryChanged: sparkCanvas.requestPaint()
                                onDisplayChanged: sparkCanvas.requestPaint()
                                onWidthChanged: sparkCanvas.requestPaint()
                                onChangeSerialChanged: {
                                    if (changeSerial > 0) {
                                        sparkCanvas.requestPaint()
                                        if (Theme.animationsEnabled) {
                                            flashAnim.restart()
                                        }
                                    }
                                }

                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                Layout.alignment: Qt.AlignTop
                                implicitHeight: cardCol.implicitHeight + Theme.s3 * 2
                                radius: Theme.radiusLg
                                color: hasError
                                    ? Theme.warningSubtle
                                    : (changed ? Theme.alpha(Theme.hueCyan, Theme.isDark ? 0.08 : 0.06) : Theme.bgSurface)
                                border.color: hasError
                                    ? Theme.alpha(Theme.warning, 0.45)
                                    : (changed ? Theme.alpha(Theme.hueCyan, 0.55) : (cardHover.hovered ? Theme.borderStrong : Theme.borderSubtle))
                                border.width: 1

                                Behavior on border.color {
                                    ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                                }
                                HoverHandler { id: cardHover }

                                // Value change delta highlight flash
                                Rectangle {
                                    id: flashRect
                                    anchors.fill: parent
                                    radius: parent.radius
                                    color: Theme.alpha(Theme.hueCyan, 0.18)
                                    opacity: 0.0
                                }

                                SequentialAnimation {
                                    id: flashAnim
                                    NumberAnimation { target: flashRect; property: "opacity"; from: 0.0; to: 1.0; duration: 90 }
                                    NumberAnimation { target: flashRect; property: "opacity"; from: 1.0; to: 0.0; duration: 510; easing.type: Easing.OutCubic }
                                }

                                ColumnLayout {
                                    id: cardCol
                                    anchors.fill: parent
                                    anchors.margins: Theme.s3
                                    spacing: Theme.s2

                                    // Top / Main Row
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Theme.s2

                                        StatusDot {
                                            size: 7
                                            color: watchCard.hasError ? Theme.warning : (watchCard.changed ? Theme.hueCyan : Theme.hueSlate)
                                            pulsing: watchCard.changed
                                        }

                                        Text {
                                            Layout.fillWidth: true
                                            text: watchCard.expr
                                            font.family: Theme.fontMono
                                            font.pixelSize: Theme.textBase
                                            font.weight: Theme.weightSemiBold
                                            color: Theme.textPrimary
                                            elide: Text.ElideRight
                                        }

                                        Badge {
                                            visible: (watchCard.typeName || "").length > 0
                                            text: watchCard.typeName || ""
                                            variant: "info"
                                            compact: true
                                        }

                                        Badge {
                                            visible: watchCard.changed && !watchCard.hasError
                                            text: qsTr("DELTA")
                                            variant: "accent"
                                            compact: true
                                        }

                                        IconButton {
                                            buttonSize: 24
                                            iconSize: 13
                                            iconName: Icons.fileCode
                                            active: watchCard.showDetails
                                            tooltipText: qsTr("Toggle type & JSON details")
                                            onClicked: watchCard.showDetails = !watchCard.showDetails
                                        }

                                        IconButton {
                                            buttonSize: 24
                                            iconSize: 13
                                            iconName: Icons.copy
                                            tooltipText: qsTr("Copy value")
                                            onClicked: {
                                                Utils.copyToClipboard(watchCard.hasError ? watchCard.error : watchCard.display)
                                                root.toastRequested(qsTr("Copied %1").arg(watchCard.expr), "info")
                                            }
                                        }

                                        IconButton {
                                            buttonSize: 24
                                            iconSize: 13
                                            iconName: Icons.trash
                                            danger: true
                                            tooltipText: qsTr("Remove watch")
                                            onClicked: Workbench.removeWatch(watchCard.expr)
                                        }
                                    }

                                    // Value + Sparkline Row (stacks or expands cleanly)
                                    RowLayout {
                                        Layout.fillWidth: true
                                        spacing: Theme.s3

                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 2

                                            Text {
                                                Layout.fillWidth: true
                                                text: watchCard.hasError ? watchCard.error : watchCard.display
                                                font.family: watchCard.hasError ? Theme.fontSans : Theme.fontMono
                                                font.pixelSize: root.compact ? Theme.textMd : Theme.textLg
                                                font.weight: Theme.weightSemiBold
                                                color: watchCard.hasError ? Theme.warning : Theme.hueCyan
                                                elide: Text.ElideRight
                                            }

                                            RowLayout {
                                                Layout.fillWidth: true
                                                spacing: Theme.s3

                                                Text {
                                                    visible: (watchCard.hex || "").length > 0
                                                    text: watchCard.hex || ""
                                                    font.family: Theme.fontMono
                                                    font.pixelSize: Theme.textXs
                                                    color: Theme.textSecondary
                                                }

                                                Text {
                                                    visible: (watchCard.addressHex || "").length > 0
                                                    text: "@ " + (watchCard.addressHex || "")
                                                    font.family: Theme.fontMono
                                                    font.pixelSize: Theme.textXs
                                                    color: Theme.textMuted
                                                    elide: Text.ElideRight
                                                }
                                            }
                                        }

                                        // Mini Sparkline Canvas for numeric values
                                        Canvas {
                                            id: sparkCanvas
                                            visible: !watchCard.hasError && watchCard.samples.length > 0
                                            Layout.preferredWidth: root.compact ? 92 : 164
                                            Layout.preferredHeight: 34
                                            onPaint: {
                                                var ctx = getContext("2d")
                                                ctx.clearRect(0, 0, width, height)
                                                var pts = watchCard.samples
                                                if (!pts || pts.length === 0) return

                                                var pad = 4
                                                var w = width - pad * 2
                                                var h = height - pad * 2
                                                var minV = pts[0]
                                                var maxV = pts[0]
                                                for (var i = 1; i < pts.length; ++i) {
                                                    if (pts[i] < minV) minV = pts[i]
                                                    if (pts[i] > maxV) maxV = pts[i]
                                                }
                                                var span = maxV - minV

                                                // Subtle baseline gradient fill when >1 point
                                                ctx.lineWidth = 1.8
                                                ctx.strokeStyle = Theme.hueCyan.toString()
                                                ctx.beginPath()

                                                if (pts.length === 1) {
                                                    var midY = pad + h / 2
                                                    ctx.moveTo(pad, midY)
                                                    ctx.lineTo(pad + w, midY)
                                                    ctx.stroke()
                                                } else {
                                                    for (var j = 0; j < pts.length; ++j) {
                                                        var px = pad + (j / (pts.length - 1)) * w
                                                        var norm = span > 0 ? (pts[j] - minV) / span : 0.5
                                                        var py = pad + h - norm * h
                                                        if (j === 0) ctx.moveTo(px, py)
                                                        else ctx.lineTo(px, py)
                                                    }
                                                    ctx.stroke()
                                                }

                                                // Draw latest sample dot
                                                var lastX = pad + w
                                                var lastNorm = span > 0 ? (pts[pts.length - 1] - minV) / span : 0.5
                                                var lastY = pts.length === 1 ? (pad + h / 2) : (pad + h - lastNorm * h)
                                                ctx.fillStyle = Theme.hueCyan.toString()
                                                ctx.beginPath()
                                                ctx.arc(lastX, lastY, 2.5, 0, Math.PI * 2)
                                                ctx.fill()
                                            }
                                        }
                                    }

                                    // Expandable Raw JSON / Type Details
                                    JsonView {
                                        visible: watchCard.showDetails
                                        Layout.fillWidth: true
                                        maxHeight: 160
                                        jsonValue: ({
                                            expr: watchCard.expr,
                                            type: watchCard.typeName || "",
                                            kind: watchCard.kind || "",
                                            address: watchCard.addressHex || "",
                                            value: watchCard.display || "",
                                            hex: watchCard.hex || "",
                                            changed: watchCard.changed,
                                            error: watchCard.error || ""
                                        })
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
