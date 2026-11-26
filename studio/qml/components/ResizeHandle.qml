import QtQuick
import McuStudio

Item {
    id: root
    property int orientation: Qt.Vertical // Qt.Vertical = left/right splitter, Qt.Horizontal = top/bottom splitter
    readonly property bool active: dragArea.pressed
    readonly property bool hovered: dragArea.containsMouse

    signal draggedDelta(real delta)
    signal dragFinished()

    implicitWidth: orientation === Qt.Vertical ? 4 : 100
    implicitHeight: orientation === Qt.Horizontal ? 4 : 100

    Rectangle {
        anchors.centerIn: parent
        width: root.orientation === Qt.Vertical ? (root.hovered || root.active ? 2 : 1) : parent.width
        height: root.orientation === Qt.Horizontal ? (root.hovered || root.active ? 2 : 1) : parent.height
        color: (root.hovered || root.active) ? Theme.accent : Theme.borderSubtle

        Behavior on color {
            ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
        }
    }

    MouseArea {
        id: dragArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: root.orientation === Qt.Vertical ? Qt.SplitHCursor : Qt.SplitVCursor
        preventStealing: true

        property real _lastGlobal: 0

        onPressed: function(mouse) {
            var g = dragArea.mapToGlobal(mouse.x, mouse.y)
            _lastGlobal = (root.orientation === Qt.Vertical) ? g.x : g.y
        }
        onPositionChanged: function(mouse) {
            if (!pressed) return
            var g = dragArea.mapToGlobal(mouse.x, mouse.y)
            var cur = (root.orientation === Qt.Vertical) ? g.x : g.y
            var delta = cur - _lastGlobal
            if (delta !== 0) {
                _lastGlobal = cur
                root.draggedDelta(delta)
            }
        }
        onReleased: root.dragFinished()
        onCanceled: root.dragFinished()
    }
}
