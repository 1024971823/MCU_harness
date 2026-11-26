pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio

Rectangle {
    id: root
    property string diffText: ""
    property int maxHeight: 300
    property bool _copied: false

    readonly property var parsedLines: {
        var raw = (diffText || "").split("\n")
        var out = []
        var oldLine = 0
        var newLine = 0
        for (var i = 0; i < raw.length; ++i) {
            var line = raw[i]
            var kind = "ctx"
            var numStr = ""
            if (line.indexOf("+++") === 0 || line.indexOf("---") === 0 || line.indexOf("diff ") === 0 || line.indexOf("index ") === 0) {
                kind = "meta"
            } else if (line.indexOf("@@") === 0) {
                kind = "hunk"
                var m = /@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)/.exec(line)
                if (m) {
                    oldLine = parseInt(m[1], 10)
                    newLine = parseInt(m[2], 10)
                }
            } else if (line.indexOf("+") === 0) {
                kind = "add"
                numStr = String(newLine > 0 ? newLine++ : "")
            } else if (line.indexOf("-") === 0) {
                kind = "del"
                numStr = String(oldLine > 0 ? oldLine++ : "")
            } else {
                if (oldLine > 0) oldLine++
                if (newLine > 0) {
                    numStr = String(newLine++)
                } else {
                    numStr = String(i + 1)
                }
            }
            out.push({ text: line, kind: kind, lineNum: numStr })
        }
        return out
    }

    implicitWidth: 460
    implicitHeight: Math.min(maxHeight, header.height + list.contentHeight + Theme.s2 + 2)
    radius: Theme.radiusLg
    color: Theme.codeBg
    border.color: Theme.borderSubtle
    border.width: 1

    Timer {
        id: copyTimer
        interval: 1500
        onTriggered: root._copied = false
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            id: header
            Layout.fillWidth: true
            Layout.preferredHeight: 28
            color: Theme.bgElevated
            topLeftRadius: Theme.radiusLg
            topRightRadius: Theme.radiusLg

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.s3
                anchors.rightMargin: Theme.s2
                spacing: Theme.s2

                Icon {
                    name: Icons.fileDiff
                    size: 13
                    color: Theme.info
                }

                Text {
                    Layout.fillWidth: true
                    text: qsTr("Unified Diff")
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.textXs
                    color: Theme.textSecondary
                }

                IconButton {
                    buttonSize: 22
                    iconSize: 13
                    iconName: root._copied ? Icons.check : Icons.copy
                    iconColor: root._copied ? Theme.success : Theme.textSecondary
                    tooltipText: root._copied ? qsTr("Copied") : qsTr("Copy diff")
                    onClicked: {
                        Utils.copyToClipboard(root.diffText)
                        root._copied = true
                        copyTimer.restart()
                    }
                }
            }
        }

        Divider { Layout.fillWidth: true }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: root.parsedLines
            ScrollBar.vertical: AppScrollBar {}

            delegate: Rectangle {
                id: diffRow
                required property var modelData
                width: list.width
                height: Math.max(20, lineText.implicitHeight + 2)
                color: {
                    switch (modelData.kind) {
                    case "add": return Theme.diffAddBg
                    case "del": return Theme.diffDelBg
                    case "hunk": return Theme.diffHunkBg
                    default: return "transparent"
                    }
                }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.s2
                    anchors.rightMargin: Theme.s2
                    spacing: Theme.s2

                    Text {
                        Layout.preferredWidth: 34
                        text: diffRow.modelData.lineNum
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.textXs
                        color: Theme.textMuted
                        horizontalAlignment: Text.AlignRight
                    }

                    Text {
                        id: lineText
                        Layout.fillWidth: true
                        text: diffRow.modelData.text
                        font.family: Theme.fontMono
                        font.pixelSize: Theme.textSm
                        wrapMode: Text.WrapAnywhere
                        color: {
                            switch (diffRow.modelData.kind) {
                            case "add": return Theme.diffAddText
                            case "del": return Theme.diffDelText
                            case "hunk": return Theme.diffHunkText
                            case "meta": return Theme.textMuted
                            default: return Theme.textPrimary
                            }
                        }
                    }
                }
            }
        }
    }
}
