pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import McuStudio

ComboBox {
    id: root
    implicitWidth: 180
    implicitHeight: 34
    focusPolicy: Qt.StrongFocus
    hoverEnabled: true

    Component {
        id: popupShadowEffect
        MultiEffect {
            shadowEnabled: true
            shadowBlur: 0.65
            shadowVerticalOffset: 6
            shadowColor: "#48000000"
        }
    }

    delegate: ItemDelegate {
        id: itemDel
        width: ListView.view ? ListView.view.width : root.width
        height: 32
        highlighted: root.highlightedIndex === index

        required property var modelData
        required property int index

        contentItem: Text {
            text: root.textRole ? (itemDel.modelData[root.textRole] ?? String(itemDel.modelData)) : String(itemDel.modelData)
            font.family: Theme.fontSans
            font.pixelSize: Theme.textBase
            font.weight: root.currentIndex === itemDel.index ? Theme.weightSemiBold : Theme.weightNormal
            color: root.currentIndex === itemDel.index ? Theme.accent : Theme.textPrimary
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }

        background: Rectangle {
            radius: Theme.radiusMd
            color: root.currentIndex === itemDel.index
                ? Theme.accentSubtle
                : (itemDel.highlighted || itemDel.hovered ? Theme.bgHover : "transparent")
        }
    }

    indicator: Icon {
        x: root.width - width - Theme.s2 - 3
        y: (root.height - height) / 2
        name: Icons.chevronDown
        size: 14
        color: root.enabled ? Theme.textSecondary : Theme.textMuted
    }

    contentItem: Text {
        leftPadding: Theme.s3
        rightPadding: Theme.s7
        text: root.displayText
        font.family: Theme.fontSans
        font.pixelSize: Theme.textBase
        color: root.enabled ? Theme.textPrimary : Theme.textMuted
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: Theme.radiusMd + 2
        color: root.enabled ? Theme.bgElevated : Theme.bgSidebar
        border.color: root.popup.opened ? Theme.accent : (root.hovered ? Theme.borderStrong : Theme.border)
        border.width: 1

        Behavior on border.color {
            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
        }

        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            radius: Theme.radiusMd + 4
            color: "transparent"
            border.color: Theme.alpha(Theme.accent, 0.45)
            border.width: Theme.focusRingWidth
            visible: root.visualFocus
        }
    }

    popup: Popup {
        y: root.height + 4
        width: root.width
        implicitHeight: Math.min(260, contentItem.implicitHeight + Theme.s2)
        padding: Theme.s1

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: root.popup.visible ? root.delegateModel : null
            currentIndex: root.highlightedIndex
            ScrollBar.vertical: AppScrollBar {}
        }

        background: Rectangle {
            id: popupBg
            color: Theme.bgSurface
            border.color: Theme.effectsEnabled ? Theme.border : Theme.borderStrong
            border.width: 1
            radius: Theme.radiusLg

            layer.enabled: Theme.effectsEnabled
            layer.effect: Theme.effectsEnabled ? popupShadowEffect : null
        }
    }
}
