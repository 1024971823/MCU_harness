import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import McuStudio

Popup {
    id: root
    property string title: ""
    property string subtitle: ""
    property string iconName: ""
    property color iconColor: Theme.accent
    property color stripColor: "transparent"
    property bool critical: false
    property int dialogWidth: 540
    default property alias body: bodyLayout.data
    property alias footer: footerRow.data

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(dialogWidth, parent ? parent.width - Theme.s8 : dialogWidth)
    modal: true
    focus: true
    padding: 0
    closePolicy: critical ? Popup.NoAutoClose : (Popup.CloseOnEscape | Popup.CloseOnPressOutside)

    Overlay.modal: Rectangle {
        color: Theme.scrim
    }

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: Theme.animNormal; easing.type: Theme.easingType }
        NumberAnimation { property: "scale"; from: 0.96; to: 1.0; duration: Theme.animNormal; easing.type: Theme.easingType }
    }

    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: Theme.animFast; easing.type: Theme.easingType }
    }

    Component {
        id: dialogShadowEffect
        MultiEffect {
            shadowEnabled: true
            shadowBlur: 0.8
            shadowVerticalOffset: 10
            shadowColor: "#55000000"
        }
    }

    background: Rectangle {
        id: dlgBg
        radius: Theme.radiusXl
        color: Theme.bgSurface
        border.color: Theme.effectsEnabled ? Theme.border : Theme.borderStrong
        border.width: 1

        layer.enabled: Theme.effectsEnabled
        layer.effect: Theme.effectsEnabled ? dialogShadowEffect : null
    }

    contentItem: ColumnLayout {
        spacing: 0

        // Optional top risk accent strip
        Rectangle {
            visible: root.stripColor !== Qt.rgba(0, 0, 0, 0) && root.stripColor !== "transparent"
            Layout.fillWidth: true
            Layout.preferredHeight: 4
            color: root.stripColor
            topLeftRadius: Theme.radiusXl
            topRightRadius: Theme.radiusXl
        }

        // Header
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: Theme.s5
            Layout.rightMargin: Theme.s4
            Layout.topMargin: Theme.s4
            Layout.bottomMargin: Theme.s3
            spacing: Theme.s3

            Rectangle {
                visible: root.iconName.length > 0
                Layout.preferredWidth: 32
                Layout.preferredHeight: 32
                radius: Theme.radiusMd
                color: Qt.rgba(root.iconColor.r, root.iconColor.g, root.iconColor.b, 0.14)

                Icon {
                    anchors.centerIn: parent
                    name: root.iconName
                    size: 18
                    color: root.iconColor
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Text {
                    Layout.fillWidth: true
                    text: root.title
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textLg
                    font.weight: Theme.weightSemiBold
                    color: Theme.textPrimary
                    elide: Text.ElideRight
                }

                Text {
                    visible: root.subtitle.length > 0
                    Layout.fillWidth: true
                    text: root.subtitle
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textSm
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                }
            }

            IconButton {
                visible: !root.critical
                buttonSize: 28
                iconSize: 15
                iconName: Icons.close
                tooltipText: qsTr("Close")
                shortcutText: "Esc"
                onClicked: root.close()
            }
        }

        Divider { Layout.fillWidth: true }

        // Body
        ColumnLayout {
            id: bodyLayout
            Layout.fillWidth: true
            Layout.margins: Theme.s5
            spacing: Theme.s3
        }

        // Footer
        Divider {
            visible: footerRow.children.length > 0
            Layout.fillWidth: true
        }

        RowLayout {
            id: footerRow
            visible: children.length > 0
            Layout.fillWidth: true
            Layout.leftMargin: Theme.s5
            Layout.rightMargin: Theme.s5
            Layout.topMargin: Theme.s3
            Layout.bottomMargin: Theme.s4
            spacing: Theme.s2
        }
    }
}
