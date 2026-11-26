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
    iconColor: Updater.updateAvailable ? Theme.hueEmerald : Theme.hueSky
    title: qsTr("Software Update")
    subtitle: Updater.updateAvailable
        ? qsTr("A new release of MCU Harness Studio is available")
        : qsTr("Check for signed updates")

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.s2

        Pill {
            hue: Theme.hueSlate
            text: "v" + (Updater.currentVersion || AppInfo.version || "0.1.0")
        }
        Icon {
            visible: Updater.updateAvailable
            name: Icons.chevronRight
            size: 14
            color: Theme.textMuted
        }
        Pill {
            visible: Updater.updateAvailable
            hue: Theme.hueEmerald
            icon: Icons.sparkles
            text: "v" + (Updater.latestVersion || "")
        }

        Item { Layout.fillWidth: true }

        Pill {
            dot: true
            dotPulsing: Updater.status === "checking" || Updater.status === "downloading"
            hue: Updater.status === "error"
                ? Theme.hueRose
                : (Updater.updateAvailable ? Theme.hueEmerald : Theme.hueSky)
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
        }
    }

    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: 204
        radius: Theme.radiusLg
        color: Theme.bgElevated
        border.color: Theme.borderSubtle
        border.width: 1

        ScrollView {
            id: notesScroll
            anchors.fill: parent
            anchors.margins: Theme.s3
            clip: true
            ScrollBar.vertical: AppScrollBar {}
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            MarkdownText {
                width: notesScroll.availableWidth
                text: Updater.releaseNotes || qsTr("You are running the latest version of MCU Harness Studio.")
            }
        }
    }

    ColumnLayout {
        visible: Updater.status === "downloading"
        Layout.fillWidth: true
        spacing: Theme.s1 + 2

        Text {
            text: qsTr("Downloading update (%1%)...").arg(Math.round((Updater.downloadProgress || 0) * 100))
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            color: Theme.textSecondary
        }

        AppProgressBar {
            Layout.fillWidth: true
            barHeight: 7
            from: 0.0
            to: 1.0
            value: Updater.downloadProgress || 0.0
        }
    }

    Rectangle {
        visible: !!(Updater.errorMessage && Updater.errorMessage.length > 0)
        Layout.fillWidth: true
        implicitHeight: updErrTxt.implicitHeight + Theme.s2 * 2
        radius: Theme.radiusMd
        color: Theme.dangerSubtle
        border.color: Theme.alpha(Theme.danger, 0.35)
        border.width: 1

        Text {
            id: updErrTxt
            anchors.fill: parent
            anchors.margins: Theme.s2
            text: Updater.errorMessage || ""
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            color: Theme.danger
            wrapMode: Text.WordWrap
        }
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
        GradientButton {
            visible: !Updater.updateAvailable
            iconName: Icons.refresh
            loading: Updater.status === "checking"
            text: qsTr("Check Now")
            onClicked: Updater.checkForUpdates()
        },
        GradientButton {
            visible: Updater.updateAvailable && Updater.status !== "ready"
            iconName: Icons.download
            loading: Updater.status === "downloading"
            text: qsTr("Download Update")
            onClicked: Updater.downloadUpdate()
        },
        GradientButton {
            visible: Updater.status === "ready"
            iconName: Icons.rotateCcw
            text: qsTr("Install & Restart")
            onClicked: Updater.installAndRestart()
        }
    ]
}
