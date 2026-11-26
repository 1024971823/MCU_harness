pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio

Item {
    id: root
    property int maxToasts: 4

    ListModel { id: toastModel }
    property var _callbacks: ({})
    property int _nextId: 1

    function show(message, level, actionLabel, callback) {
        var tid = _nextId++
        if (toastModel.count >= maxToasts) {
            var oldest = toastModel.get(0)
            delete _callbacks[oldest.toastId]
            toastModel.remove(0)
        }
        if (callback) {
            _callbacks[tid] = callback
        }
        toastModel.append({
            toastId: tid,
            toastMessage: message || "",
            toastLevel: level || "info",
            toastActionLabel: actionLabel || ""
        })
    }

    function dismiss(toastId) {
        delete _callbacks[toastId]
        for (var i = 0; i < toastModel.count; ++i) {
            if (toastModel.get(i).toastId === toastId) {
                toastModel.remove(i)
                break
            }
        }
    }

    function triggerAction(toastId) {
        var cb = _callbacks[toastId]
        dismiss(toastId)
        if (cb) cb()
    }

    ColumnLayout {
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: Theme.s4
        spacing: Theme.s2
        width: 360

        Repeater {
            model: toastModel

            delegate: Toast {
                id: toastItem
                required property int toastId
                required property string toastMessage
                required property string toastLevel
                required property string toastActionLabel

                Layout.fillWidth: true
                message: toastItem.toastMessage
                level: toastItem.toastLevel
                actionText: toastItem.toastActionLabel

                onActionClicked: root.triggerAction(toastItem.toastId)
                onClosed: root.dismiss(toastItem.toastId)

                Timer {
                    interval: 5000
                    running: true
                    onTriggered: root.dismiss(toastItem.toastId)
                }
            }
        }
    }
}
