pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Dialogs
import QtQuick.Layouts
import McuStudio
import "../components"

AppDialog {
    id: root
    signal completed()

    property int stepIndex: 0
    property string selectedDir: (Workbench.target && Workbench.target.projectDir) ? Workbench.target.projectDir : ""
    property string draftProvider: Settings.provider || "deepseek"
    property string draftModel: Settings.model || "deepseek-chat"
    property string draftApiKey: Settings.apiKey || ""
    property string draftMode: Workbench.interlockMode || "interactive"

    dialogWidth: 720
    iconName: Icons.sparkles
    title: qsTr("Welcome to MCU Harness Studio")
    subtitle: qsTr("Configure your workspace, AI model provider, and hardware safety interlocks")

    FolderDialog {
        id: wizFolderDlg
        title: qsTr("Select Embedded Firmware Project")
        onAccepted: root.selectedDir = selectedFolder
    }

    // 4-Step Progress Stepper
    RowLayout {
        Layout.fillWidth: true
        spacing: Theme.s2

        Repeater {
            model: [
                { num: "1", label: qsTr("Welcome") },
                { num: "2", label: qsTr("Project") },
                { num: "3", label: qsTr("AI Model") },
                { num: "4", label: qsTr("Safety & License") }
            ]

            delegate: Rectangle {
                id: stepPill
                required property var modelData
                required property int index
                readonly property bool current: root.stepIndex === index
                readonly property bool done: root.stepIndex > index

                Layout.fillWidth: true
                implicitHeight: 34
                radius: Theme.radiusMd
                color: current ? Theme.accentSubtle : (done ? Theme.successSubtle : Theme.bgElevated)
                border.color: current ? Theme.accent : (done ? Theme.success : Theme.borderSubtle)
                border.width: 1

                TapHandler { onTapped: root.stepIndex = stepPill.index }

                RowLayout {
                    anchors.centerIn: parent
                    spacing: Theme.s2

                    Rectangle {
                        Layout.preferredWidth: 18
                        Layout.preferredHeight: 18
                        radius: 9
                        color: stepPill.current ? Theme.accent : (stepPill.done ? Theme.success : Theme.borderStrong)

                        Text {
                            anchors.centerIn: parent
                            text: stepPill.done ? "✓" : stepPill.modelData.num
                            font.family: Theme.fontSans
                            font.pixelSize: 10
                            font.weight: Theme.weightBold
                            color: Theme.textOnAccent
                        }
                    }

                    Text {
                        text: stepPill.modelData.label
                        font.family: Theme.fontSans
                        font.pixelSize: Theme.textSm
                        font.weight: stepPill.current ? Theme.weightSemiBold : Theme.weightMedium
                        color: stepPill.current ? Theme.textPrimary : Theme.textSecondary
                    }
                }
            }
        }
    }

    Divider { Layout.fillWidth: true }

    // Step Content Container (fixed clean height ~ 280px)
    Item {
        Layout.fillWidth: true
        Layout.preferredHeight: 280

        // STEP 1: Welcome, Theme, Language
        ColumnLayout {
            visible: root.stepIndex === 0
            anchors.fill: parent
            spacing: Theme.s4

            Text {
                Layout.fillWidth: true
                text: qsTr("Closed-loop embedded AI engineering with zero-compromise hardware safety.")
                font.family: Theme.fontSans
                font.pixelSize: Theme.textMd
                font.weight: Theme.weightMedium
                color: Theme.textPrimary
                wrapMode: Text.WordWrap
            }

            SettingRow {
                title: qsTr("Appearance")
                description: qsTr("Choose Dark, Light, or System theme")

                SegmentedControl {
                    currentValue: Settings.theme || "system"
                    items: [
                        { value: "system", label: qsTr("System"), icon: Icons.monitor },
                        { value: "dark",   label: qsTr("Dark"),   icon: Icons.moon },
                        { value: "light",  label: qsTr("Light"),  icon: Icons.sun }
                    ]
                    onActivated: function(val) { Settings.setTheme(val) }
                }
            }

            SettingRow {
                title: qsTr("Language")
                description: qsTr("Interface localization")

                SegmentedControl {
                    currentValue: Settings.language || "en"
                    items: [
                        { value: "en",    label: "English" },
                        { value: "zh_CN", label: "简体中文" }
                    ]
                    onActivated: function(val) { Settings.setLanguage(val) }
                }
            }

            Item { Layout.fillHeight: true }
        }

        // STEP 2: Project Directory
        ColumnLayout {
            visible: root.stepIndex === 1
            anchors.fill: parent
            spacing: Theme.s4

            Text {
                Layout.fillWidth: true
                text: qsTr("Select your embedded firmware repository (CMake, Makefile, .ioc, .svd, and ELF build directory).")
                font.family: Theme.fontSans
                font.pixelSize: Theme.textBase
                color: Theme.textSecondary
                wrapMode: Text.WordWrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2

                AppTextField {
                    Layout.fillWidth: true
                    monospace: true
                    placeholderText: "/path/to/stm32h563-firmware"
                    text: root.selectedDir
                    onTextEdited: root.selectedDir = text
                }

                AppButton {
                    variant: "secondary"
                    iconName: Icons.folderOpen
                    text: qsTr("Browse...")
                    onClicked: wizFolderDlg.open()
                }
            }

            Card {
                Layout.fillWidth: true
                iconName: Icons.microchip
                title: qsTr("Auto-Discovery")
                Text {
                    Layout.fillWidth: true
                    text: qsTr("MCU Harness automatically discovers mcu-harness.toml, STM32CubeMX .ioc pinouts, CMSIS-SVD files, and build/*.elf symbols.")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textSm
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                }
            }

            Item { Layout.fillHeight: true }
        }

        // STEP 3: AI Model + Offline Mock Skip
        ColumnLayout {
            visible: root.stepIndex === 2
            anchors.fill: parent
            spacing: Theme.s3

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s3

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    spacing: Theme.s1
                    Text { text: qsTr("Provider"); font.family: Theme.fontSans; font.pixelSize: Theme.textSm; color: Theme.textSecondary }
                    AppComboBox {
                        Layout.fillWidth: true
                        model: ["deepseek", "openai", "qwen", "openrouter", "anthropic", "gemini", "ollama", "vllm", "mock"]
                        currentIndex: Math.max(0, ["deepseek", "openai", "qwen", "openrouter", "anthropic", "gemini", "ollama", "vllm", "mock"].indexOf(root.draftProvider))
                        onActivated: root.draftProvider = currentText
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 1
                    spacing: Theme.s1
                    Text { text: qsTr("Model ID"); font.family: Theme.fontSans; font.pixelSize: Theme.textSm; color: Theme.textSecondary }
                    AppTextField {
                        Layout.fillWidth: true
                        monospace: true
                        placeholderText: "deepseek-chat"
                        text: root.draftModel
                        onTextEdited: root.draftModel = text
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: Theme.s1
                Text { text: qsTr("API Key"); font.family: Theme.fontSans; font.pixelSize: Theme.textSm; color: Theme.textSecondary }
                AppTextField {
                    Layout.fillWidth: true
                    passwordField: true
                    monospace: true
                    placeholderText: "sk-..."
                    text: root.draftApiKey
                    onTextEdited: root.draftApiKey = text
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.s2

                AppButton {
                    buttonSize: "sm"
                    variant: "secondary"
                    iconName: Icons.zap
                    loading: !!Settings.testingConnection
                    text: qsTr("Test Connection")
                    onClicked: Workbench.testModelConnection(root.draftProvider, root.draftModel, "", root.draftApiKey)
                }

                AppButton {
                    buttonSize: "sm"
                    variant: "ghost"
                    iconName: Icons.flask
                    text: qsTr("Use Offline Mock Mode")
                    onClicked: {
                        root.draftProvider = "mock"
                        root.draftModel = "mock-model"
                        root.draftApiKey = ""
                        root.stepIndex = 3
                    }
                }
            }

            Item { Layout.fillHeight: true }
        }

        // STEP 4: Safety & License
        ColumnLayout {
            visible: root.stepIndex === 3
            anchors.fill: parent
            spacing: Theme.s3

            Text {
                text: qsTr("Select Default Hardware Safety Interlock")
                font.family: Theme.fontSans
                font.pixelSize: Theme.textBase
                font.weight: Theme.weightSemiBold
                color: Theme.textPrimary
            }

            SegmentedControl {
                Layout.fillWidth: true
                currentValue: root.draftMode
                items: [
                    { value: "interactive", label: qsTr("Interactive (Recommended)"), icon: Icons.shieldAlert },
                    { value: "auto_safe",   label: qsTr("Auto-Safe"), icon: Icons.shieldCheck },
                    { value: "read_only",   label: qsTr("Read-Only"), icon: Icons.lock }
                ]
                onActivated: function(val) { root.draftMode = val }
            }

            Card {
                Layout.fillWidth: true
                iconName: Icons.keyRound
                title: qsTr("Trial & License")
                Text {
                    Layout.fillWidth: true
                    text: License.status === "active"
                        ? qsTr("Active %1 license verified.").arg(License.tier || "Pro")
                        : qsTr("Your 14-day Pro Trial is active (%1 days remaining). You can activate a license key anytime in Settings.").arg(License.trialDaysRemaining || 14)
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textSm
                    color: Theme.textSecondary
                    wrapMode: Text.WordWrap
                }
            }

            Item { Layout.fillHeight: true }
        }
    }

    footer: [
        Text {
            text: qsTr("Step %1 of 4").arg(root.stepIndex + 1)
            font.family: Theme.fontSans
            font.pixelSize: Theme.textSm
            color: Theme.textMuted
        },
        Item { Layout.fillWidth: true },
        AppButton {
            visible: root.stepIndex > 0
            variant: "secondary"
            text: qsTr("Back")
            onClicked: root.stepIndex--
        },
        AppButton {
            variant: "ghost"
            text: qsTr("Skip")
            onClicked: {
                Settings.firstRunCompleted = true
                root.close()
            }
        },
        AppButton {
            variant: "primary"
            text: root.stepIndex < 3 ? qsTr("Next") : qsTr("Finish Setup")
            onClicked: {
                if (root.stepIndex < 3) {
                    root.stepIndex++
                } else {
                    if (root.selectedDir.trim().length > 0) {
                        Workbench.openProject(root.selectedDir)
                    }
                    Workbench.applyModelConfig(root.draftProvider, root.draftModel, "", root.draftApiKey, true)
                    Workbench.setInterlockMode(root.draftMode)
                    Settings.firstRunCompleted = true
                    root.completed()
                    root.close()
                }
            }
        }
    ]
}
