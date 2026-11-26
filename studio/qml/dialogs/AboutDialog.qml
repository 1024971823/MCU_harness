import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

AppDialog {
    id: root
    signal toastRequested(string message, string level)

    dialogWidth: 540
    title: qsTr("About MCU Harness Studio")
    subtitle: qsTr("Hardware-aware embedded AI debugging workbench")

    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.s4

        Image {
            Layout.preferredWidth: 56
            Layout.preferredHeight: 56
            source: "qrc:/qt/qml/McuStudio/resources/app/mcu-harness-studio.svg"
            sourceSize: Qt.size(56, 56)
            smooth: true
            mipmap: true
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                text: qsTr("MCU Harness Studio")
                font.family: Theme.fontSans
                font.pixelSize: Theme.textLg
                font.weight: Theme.weightSemiBold
                color: Theme.textPrimary
            }

            KeyValueRow { label: qsTr("Studio"); value: AppInfo.version || "0.1.0"; monospace: true }
            KeyValueRow { label: qsTr("Engine"); value: AppInfo.engineVersion || "0.1.0"; monospace: true }
            KeyValueRow { label: qsTr("Commit"); value: AppInfo.gitHash || "dev"; monospace: true; copyable: true }
        }
    }

    Text {
        text: qsTr("Third-Party Open Source Notices")
        font.family: Theme.fontSans
        font.pixelSize: Theme.textSm
        font.weight: Theme.weightSemiBold
        color: Theme.textSecondary
    }

    CodeBlock {
        Layout.fillWidth: true
        maxHeight: 190
        language: "NOTICE"
        text: (AppInfo.thirdPartyLicenses && AppInfo.thirdPartyLicenses.length > 0)
            ? AppInfo.thirdPartyLicenses
            : "Qt 6 (LGPLv3 / Commercial)\nInter Font (SIL Open Font License 1.1)\nJetBrains Mono Font (SIL Open Font License 1.1)\nLucide Icons (ISC License)\nTweetNaCl (Public Domain)"
    }

    footer: [
        Item { Layout.fillWidth: true },
        AppButton {
            variant: "primary"
            text: qsTr("Close")
            onClicked: root.close()
        }
    ]
}
