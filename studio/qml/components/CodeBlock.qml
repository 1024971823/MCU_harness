import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio

Rectangle {
    id: root
    property string text: ""
    property string language: ""
    property int maxHeight: 280
    property bool _copied: false

    implicitWidth: 420
    implicitHeight: Math.min(maxHeight, headerBar.height + codeScroll.implicitHeight + 2)
    radius: Theme.radiusLg
    color: Theme.codeBg
    border.color: Theme.borderSubtle
    border.width: 1

    Timer {
        id: resetCopy
        interval: 1500
        onTriggered: root._copied = false
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            id: headerBar
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

                Text {
                    Layout.fillWidth: true
                    text: root.language.length > 0 ? root.language : qsTr("Code")
                    font.family: Theme.fontMono
                    font.pixelSize: Theme.textXs
                    color: Theme.textSecondary
                    elide: Text.ElideRight
                }

                IconButton {
                    buttonSize: 22
                    iconSize: 13
                    iconName: root._copied ? Icons.check : Icons.copy
                    iconColor: root._copied ? Theme.success : Theme.textSecondary
                    tooltipText: root._copied ? qsTr("Copied") : qsTr("Copy")
                    onClicked: {
                        Utils.copyToClipboard(root.text)
                        root._copied = true
                        resetCopy.restart()
                    }
                }
            }
        }

        Divider { Layout.fillWidth: true }

        ScrollView {
            id: codeScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            implicitHeight: Math.max(48, codeEdit.contentHeight + Theme.s3 * 2)
            clip: true
            ScrollBar.vertical: AppScrollBar {}
            ScrollBar.horizontal: AppScrollBar {}

            TextEdit {
                id: codeEdit
                width: Math.max(codeScroll.availableWidth, implicitWidth)
                text: root.text
                readOnly: true
                selectByMouse: true
                font.family: Theme.fontMono
                font.pixelSize: Theme.textSm
                color: Theme.textPrimary
                selectionColor: Theme.accentSubtle
                selectedTextColor: Theme.textPrimary
                padding: Theme.s3
            }
        }
    }
}
