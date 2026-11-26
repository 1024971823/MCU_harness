import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import McuStudio
import "../components"

AppDialog {
    id: root
    signal toastRequested(string message, string level)

    readonly property color statusHue: License.status === "active"
        ? Theme.hueEmerald
        : (License.status === "expired" ? Theme.hueRose : Theme.accent)

    dialogWidth: 540
    iconName: Icons.keyRound
    iconColor: statusHue
    stripColor: statusHue
    title: qsTr("License Manager")
    subtitle: qsTr("Offline Ed25519 signed license verification & trial status")

    FileDialog {
        id: licFileDlg
        title: qsTr("Import License File")
        nameFilters: [qsTr("License files (*.lic *.json *.txt)"), qsTr("All files (*)")]
        onAccepted: {
            License.importLicenseFile(selectedFile)
            root.toastRequested(qsTr("License file processed"), License.status === "active" ? "success" : "warning")
        }
    }

    Card {
        Layout.fillWidth: true
        iconName: Icons.badgeCheck
        title: License.status === "active"
            ? qsTr("Active License — %1").arg(License.tier || "Pro")
            : (License.status === "expired" ? qsTr("Trial Expired") : qsTr("Evaluation Trial"))

        headerActions: [
            Pill {
                dot: true
                hue: root.statusHue
                text: (License.status || "trial").toUpperCase()
            }
        ]

        ColumnLayout {
            visible: License.status !== "active"
            Layout.fillWidth: true
            spacing: Theme.s2

            Text {
                Layout.fillWidth: true
                text: qsTr("%1 of %2 trial days remaining").arg(License.trialDaysRemaining || 0).arg(License.trialDaysTotal || 14)
                font.family: Theme.fontSans
                font.pixelSize: Theme.textSm
                color: Theme.textSecondary
            }

            AppProgressBar {
                Layout.fillWidth: true
                barHeight: 7
                from: 0.0
                to: 1.0
                value: License.trialProgress !== undefined ? License.trialProgress : 0.7
                fillColor: root.statusHue
            }
        }

        KeyValueRow {
            visible: License.status === "active"
            label: qsTr("Licensed To")
            value: License.licensedTo || "—"
        }
        KeyValueRow {
            visible: License.status === "active"
            label: qsTr("Expires")
            value: License.expiresAt || qsTr("Perpetual")
            monospace: true
        }
        KeyValueRow {
            label: qsTr("Machine ID")
            value: License.machineId || "MCU-DEV-0000"
            monospace: true
            copyable: true
        }
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: Theme.s1 + 2

        Text {
            text: qsTr("Paste Signed License Key")
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            font.weight: Theme.weightMedium
            color: Theme.textSecondary
        }

        AppTextArea {
            id: keyInput
            Layout.fillWidth: true
            Layout.preferredHeight: 84
            monospace: true
            placeholderText: "MCUHARNESS-PRO-..."
        }
    }

    Rectangle {
        visible: !!(License.lastError && License.lastError.length > 0)
        Layout.fillWidth: true
        implicitHeight: licErrTxt.implicitHeight + Theme.s2 * 2
        radius: Theme.radiusMd
        color: Theme.dangerSubtle
        border.color: Theme.alpha(Theme.danger, 0.35)
        border.width: 1

        Text {
            id: licErrTxt
            anchors.fill: parent
            anchors.margins: Theme.s2
            text: License.lastError || ""
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            color: Theme.danger
            wrapMode: Text.WordWrap
        }
    }

    footer: [
        AppButton {
            variant: "secondary"
            iconName: Icons.upload
            text: qsTr("Import File...")
            onClicked: licFileDlg.open()
        },
        AppButton {
            visible: License.status === "active"
            variant: "ghost"
            text: qsTr("Deactivate")
            onClicked: {
                License.deactivate()
                root.toastRequested(qsTr("License deactivated"), "info")
            }
        },
        Item { Layout.fillWidth: true },
        AppButton {
            variant: "ghost"
            text: qsTr("Close")
            onClicked: root.close()
        },
        GradientButton {
            iconName: Icons.badgeCheck
            text: qsTr("Activate")
            enabled: keyInput.text.trim().length > 0
            onClicked: {
                License.activateKey(keyInput.text.trim())
                if (License.status === "active") {
                    root.toastRequested(qsTr("License activated!"), "success")
                    keyInput.text = ""
                }
            }
        }
    ]
}
