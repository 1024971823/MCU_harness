pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio

FocusScope {
    id: root
    property var items: [] // Array of { value, label, icon? } or strings
    property string currentValue: ""
    property int currentIndex: 0
    property bool compact: false

    signal activated(string value, int index)

    implicitWidth: row.implicitWidth + 6
    implicitHeight: compact ? 28 : 34
    activeFocusOnTab: true

    onCurrentValueChanged: {
        for (var i = 0; i < items.length; ++i) {
            var v = typeof items[i] === "string" ? items[i] : (items[i].value ?? "")
            if (v === currentValue) {
                currentIndex = i
                break
            }
        }
    }

    Keys.onLeftPressed: {
        if (items.length > 0 && currentIndex > 0) {
            selectIdx(currentIndex - 1)
        }
    }
    Keys.onRightPressed: {
        if (items.length > 0 && currentIndex < items.length - 1) {
            selectIdx(currentIndex + 1)
        }
    }

    function selectIdx(idx) {
        if (idx < 0 || idx >= items.length) return
        currentIndex = idx
        var val = typeof items[idx] === "string" ? items[idx] : (items[idx].value ?? "")
        currentValue = val
        activated(val, idx)
    }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusMd + 2
        color: Theme.bgElevated
        border.color: Theme.border
        border.width: 1

        Rectangle {
            anchors.fill: parent
            anchors.margins: -3
            radius: Theme.radiusMd + 4
            color: "transparent"
            border.color: Theme.accent
            border.width: Theme.focusRingWidth
            visible: root.activeFocus
        }

        RowLayout {
            id: row
            anchors.fill: parent
            anchors.margins: 3
            spacing: 2

            Repeater {
                model: root.items

                delegate: Rectangle {
                    id: seg
                    required property var modelData
                    required property int index

                    readonly property string itemValue: typeof modelData === "string" ? modelData : (modelData.value ?? "")
                    readonly property string itemLabel: typeof modelData === "string" ? modelData : (modelData.label ?? itemValue)
                    readonly property string itemIcon: typeof modelData === "string" ? "" : (modelData.icon ?? "")
                    readonly property bool selected: root.currentValue.length > 0
                        ? (itemValue === root.currentValue)
                        : (index === root.currentIndex)

                    Layout.fillHeight: true
                    implicitWidth: segRow.implicitWidth + Theme.s3 * 2
                    radius: Theme.radiusMd
                    color: selected ? Theme.bgSurface : (segHover.hovered ? Theme.bgHover : "transparent")
                    border.color: selected ? Theme.border : "transparent"
                    border.width: selected ? 1 : 0

                    Behavior on color {
                        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                    }

                    HoverHandler { id: segHover }
                    TapHandler {
                        onTapped: {
                            root.forceActiveFocus(Qt.MouseFocusReason)
                            root.selectIdx(seg.index)
                        }
                    }

                    RowLayout {
                        id: segRow
                        anchors.centerIn: parent
                        spacing: Theme.s1 + 2

                        Icon {
                            visible: seg.itemIcon.length > 0
                            name: seg.itemIcon
                            size: 13
                            color: seg.selected ? Theme.accent : Theme.textSecondary
                        }

                        Text {
                            text: seg.itemLabel
                            font.family: Theme.fontSans
                            font.pixelSize: root.compact ? Theme.textXs : Theme.textSm
                            font.weight: seg.selected ? Theme.weightSemiBold : Theme.weightMedium
                            color: seg.selected ? Theme.textPrimary : Theme.textSecondary
                        }
                    }
                }
            }
        }
    }
}
