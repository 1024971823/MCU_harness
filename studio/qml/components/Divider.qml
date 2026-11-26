import QtQuick
import McuStudio

Rectangle {
    id: root
    property int orientation: Qt.Horizontal

    implicitWidth: orientation === Qt.Horizontal ? 1 : 1
    implicitHeight: orientation === Qt.Horizontal ? 1 : 1
    color: Theme.borderSubtle
}
