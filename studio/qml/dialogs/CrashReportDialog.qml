import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import McuStudio
import "../components"

AppDialog {
    id: root
    signal toastRequested(string message, string level)

    dialogWidth: 620
    iconName: Icons.triangleAlert
    iconColor: Theme.danger
    stripColor: Theme.danger
    title: qsTr("Previous Session Crash Detected")
    subtitle: qsTr("A sanitized diagnostics snapshot was saved from the previous unexpected exit")

    FileDialog {
        id: exportDlg
        title: qsTr("Export Crash Diagnostics Bundle")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("Zip archives (*.zip)")]
        onAccepted: {
            Diagnostics.exportBundle(selectedFile)
            root.toastRequested(qsTr("Crash bundle exported"), "success")
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.s4

        KeyValueRow {
            Layout.fillWidth: true
            label: qsTr("Timestamp")
            value: Diagnostics.crashTime || "—"
            monospace: true
        }
        KeyValueRow {
            Layout.fillWidth: true
            label: qsTr("Signal")
            value: Diagnostics.crashSignal || "SIGSEGV"
            monospace: true
            valueColor: Theme.danger
        }
    }

    CodeBlock {
        Layout.fillWidth: true
        maxHeight: 240
        language: qsTr("Sanitized Backtrace")
        text: Diagnostics.crashBacktrace || qsTr("No stack frames captured.")
    }

    Text {
        Layout.fillWidth: true
        text: qsTr("API keys, workspace paths, and firmware binaries are automatically scrubbed before export or submission.")
        font.family: Theme.fontSans
        font.pixelSize: Theme.textXs
        color: Theme.textMuted
        wrapMode: Text.WordWrap
    }

    footer: [
        AppButton {
            variant: "secondary"
            iconName: Icons.fileDown
            text: qsTr("Export Bundle...")
            onClicked: exportDlg.open()
        },
        Item { Layout.fillWidth: true },
        AppButton {
            variant: "ghost"
            text: qsTr("Dismiss")
            onClicked: {
                Diagnostics.dismissCrashReport()
                root.close()
            }
        },
        AppButton {
            variant: "primary"
            iconName: Icons.send
            text: qsTr("Send Report")
            onClicked: {
                Diagnostics.sendCrashReport()
                root.toastRequested(qsTr("Crash report sent — thank you"), "success")
                root.close()
            }
        }
    ]
}
