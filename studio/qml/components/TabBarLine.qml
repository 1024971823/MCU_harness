pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio

Item {
    id: root
    property var tabs: [] // Array of { id, label, icon?, badge? }
    property string currentId: ""
    property int currentIndex: 0

    signal tabSelected(string tabId, int index)

    implicitWidth: row.implicitWidth
    implicitHeight: 36

    RowLayout {
        id: row
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        spacing: Theme.s1

        Repeater {
            model: root.tabs

            delegate: Item {
                id: tabItem
                required property var modelData
                required property int index

                readonly property string itemId: modelData.id ?? String(index)
                readonly property string itemLabel: modelData.label ?? itemId
                readonly property string itemIcon: modelData.icon ?? ""
                readonly property string itemBadge: modelData.badge !== undefined && modelData.badge !== null ? String(modelData.badge) : ""
                readonly property bool selected: root.currentId.length > 0
                    ? (itemId === root.currentId)
                    : (index === root.currentIndex)

                Layout.fillHeight: true
                implicitWidth: tabRow.implicitWidth + Theme.s3 * 2

                HoverHandler { id: tabHover }
                TapHandler {
                    onTapped: {
                        root.currentIndex = tabItem.index
                        root.currentId = tabItem.itemId
                        root.tabSelected(tabItem.itemId, tabItem.index)
                    }
                }

                Rectangle {
                    anchors.fill: parent
                    anchors.margins: 4
                    radius: Theme.radiusSm
                    color: tabHover.hovered && !tabItem.selected ? Theme.bgHover : "transparent"
                    Behavior on color { ColorAnimation { duration: Theme.animFast } }
                }

                RowLayout {
                    id: tabRow
                    anchors.centerIn: parent
                    spacing: Theme.s1 + 2

                    Icon {
                        visible: tabItem.itemIcon.length > 0
                        name: tabItem.itemIcon
                        size: 13
                        color: tabItem.selected ? Theme.accent : Theme.textSecondary
                    }

                    Text {
                        text: tabItem.itemLabel
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textSm
                        font.weight: tabItem.selected ? Theme.weightSemiBold : Theme.weightMedium
                        color: tabItem.selected ? Theme.textPrimary : Theme.textSecondary
                    }

                    Badge {
                        visible: tabItem.itemBadge.length > 0
                        text: tabItem.itemBadge
                        variant: tabItem.selected ? "accent" : "neutral"
                        compact: true
                    }
                }

                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 2
                    radius: 1
                    color: Theme.accent
                    visible: tabItem.selected
                }
            }
        }
    }
}
