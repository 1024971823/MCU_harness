import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import McuStudio
import "../components"

AppDialog {
    id: root
    signal toastRequested(string message, string level)

    dialogWidth: 540
    iconName: Icons.download
    iconColor: Theme.accent
    title: qsTr("Software Update")
    subtitle: Updater.updateAvailable
        ? qsTr("A new release of MCU Harness Studio is available")
        : qsTr("Check for signed updates")

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.s3

        Badge { text: "v" + (Updater.currentVersion || AppInfo.version || "0.1.0"); variant: "neutral" }
        Icon { visible: Updater.updateAvailable; name: Icons.chevronRight; size: 14; color: Theme.textMuted }
        Badge { visible: Updater.updateAvailable; text: "v" + (Updater.latestVersion || ""); variant: "success" }
        Item { Layout.fillWidth: true }
        Badge {
            text: {
                if (Updater.status === "checking") return qsTr("Checking")
                if (Updater.status === "downloading") return qsTr("Downloading")
                if (Updater.status === "verifying") return qsTr("Verifying")
                if (Updater.status === "ready") return qsTr("Ready to Install")
                if (Updater.status === "error") return qsTr("Error")
                if (Updater.updateAvailable) return qsTr("Update Available")
                if (Updater.status === "upToDate") return qsTr("Up to Date")
                return qsTr("Ready")
            }
            variant: Updater.status === "error" ? "danger" : (Updater.updateAvailable ? "accent" : "neutral")
            compact: true
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 200
        radius: Theme.radiusMd
        color: Theme.bgElevated
        border.color: Theme.borderSubtle
        border.width: 1

        ScrollView {
            anchors.fill: parent
            anchors.margins: Theme.s3
            clip: true
            ScrollBar.vertical: AppScrollBar {}
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            MarkdownText {
                width: parent.width
                text: Updater.releaseNotes || qsTr("You are running the latest version of MCU Harness Studio.")
            }
        }
    }

    ColumnLayout {
        visible: Updater.status === "downloading"
        Layout.fillWidth: true
        spacing: Theme.s1

        Text {
            text: qsTr("Downloading update (%1%)...").arg(Math.round((Updater.downloadProgress || 0) * 100))
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            color: Theme.textSecondary
        }

        AppProgressBar {
            Layout.fillWidth: true
            from: 0.0
            to: 1.0
            value: Updater.downloadProgress || 0.0
        }
    }

    Text {
        visible: !!(Updater.errorMessage && Updater.errorMessage.length > 0)
        Layout.fillWidth: true
        text: Updater.errorMessage || ""
        font.family: Theme.fontSans
        font.pixelSize: Theme.textSm
        color: Theme.danger
        wrapMode: Text.WordWrap
    }

    footer: [
        AppButton {
            visible: Updater.updateAvailable
            variant: "ghost"
            text: qsTr("Skip Version")
            onClicked: {
                Updater.skipVersion(Updater.latestVersion)
                root.close()
            }
        },
        Item { Layout.fillWidth: true },
        AppButton {
            variant: "secondary"
            text: qsTr("Later")
            onClicked: root.close()
        },
        AppButton {
            visible: !Updater.updateAvailable
            variant: "primary"
            iconName: Icons.refresh
            loading: Updater.status === "checking"
            text: qsTr("Check Now")
            onClicked: Updater.checkForUpdates()
        },
        AppButton {
            visible: Updater.updateAvailable && Updater.status !== "ready"
            variant: "primary"
            iconName: Icons.download
            loading: Updater.status === "downloading"
            text: qsTr("Download Update")
            onClicked: Updater.downloadUpdate()
        },
        AppButton {
            visible: Updater.status === "ready"
            variant: "primary"
            iconName: Icons.rotateCcw
            text: qsTr("Install & Restart")
            onClicked: Updater.installAndRestart()
        }
    ]
}
