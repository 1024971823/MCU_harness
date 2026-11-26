pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import McuStudio
import "../components"

Item {
    id: root

    signal openLicenseRequested()
    signal openUpdateRequested()
    signal openAboutRequested()
    signal openToast(string message, string level)

    property string activeSection: "general"
    property string searchFilter: ""

    property string draftProvider: Settings.provider || "mock"
    property string draftModel: Settings.model || ""
    property string draftBaseUrl: Settings.baseUrl || ""
    property string draftApiKey: Settings.apiKey || ""
    property bool draftRememberKey: Settings.rememberKey !== undefined ? Settings.rememberKey : true

    readonly property var sections: [
        { id: "general",    icon: Icons.settings2, label: qsTr("General") },
        { id: "appearance", icon: Icons.moon,      label: qsTr("Appearance") },
        { id: "language",   icon: Icons.languages, label: qsTr("Language") },
        { id: "model",      icon: Icons.sparkles,  label: qsTr("AI Model") },
        { id: "hardware",   icon: Icons.hardware,  label: qsTr("Hardware") },
        { id: "safety",     icon: Icons.shield,    label: qsTr("Safety Interlock") },
        { id: "updates",    icon: Icons.download,  label: qsTr("Updates") },
        { id: "privacy",    icon: Icons.fileDown,  label: qsTr("Privacy & Diagnostics") },
        { id: "license",    icon: Icons.keyRound,  label: qsTr("License") },
        { id: "about",      icon: Icons.info,      label: qsTr("About") }
    ]

    readonly property var providers: [
        { id: "openai",     name: "OpenAI",     defaultModel: "gpt-4o",                         hint: "api.openai.com/v1",                      needsKey: true },
        { id: "deepseek",   name: "DeepSeek",   defaultModel: "deepseek-chat",                  hint: "api.deepseek.com/v1",                    needsKey: true },
        { id: "qwen",       name: "Qwen",       defaultModel: "qwen-plus",                      hint: "dashscope.aliyuncs.com/compatible-mode", needsKey: true },
        { id: "openrouter", name: "OpenRouter", defaultModel: "anthropic/claude-3.7-sonnet",    hint: "openrouter.ai/api/v1",                   needsKey: true },
        { id: "anthropic",  name: "Anthropic",  defaultModel: "claude-sonnet-4-20250514",       hint: "api.anthropic.com",                      needsKey: true },
        { id: "gemini",     name: "Gemini",     defaultModel: "gemini-2.5-flash",               hint: "generativelanguage.googleapis.com",      needsKey: true },
        { id: "ollama",     name: "Ollama",     defaultModel: "qwen2.5-coder:14b",              hint: "127.0.0.1:11434/v1",                     needsKey: false },
        { id: "vllm",       name: "vLLM",       defaultModel: "default",                        hint: "127.0.0.1:8000/v1",                      needsKey: false },
        { id: "mock",       name: "Mock (Offline)", defaultModel: "mock-model",                 hint: "Local demo simulation",                  needsKey: false }
    ]

    function sectionVisible(secId) {
        return searchFilter.trim().length > 0 || activeSection === secId
    }

    FolderDialog {
        id: projPicker
        title: qsTr("Select Embedded Firmware Project")
        onAccepted: {
            Workbench.openProject(selectedFolder)
            root.openToast(qsTr("Project switched"), "success")
        }
    }

    FileDialog {
        id: diagExportDlg
        title: qsTr("Export Diagnostics Bundle")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("Zip archives (*.zip)")]
        onAccepted: {
            Diagnostics.exportBundle(selectedFile)
            root.openToast(qsTr("Diagnostics bundle exported"), "success")
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // Left Settings Navigation Rail
        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 210
            color: Theme.bgSidebar

            Divider {
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.right: parent.right
                orientation: Qt.Vertical
                width: 1
            }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme.s3
                spacing: Theme.s1

                Text {
                    Layout.leftMargin: Theme.s2
                    Layout.bottomMargin: Theme.s2
                    text: qsTr("Settings")
                    font.family: Theme.fontSans
                    font.pixelSize: Theme.textLg
                    font.weight: Theme.weightSemiBold
                    color: Theme.textPrimary
                }

                Repeater {
                    model: root.sections

                    delegate: Rectangle {
                        id: secBtn
                        required property var modelData
                        readonly property bool selected: root.activeSection === modelData.id && root.searchFilter.trim().length === 0

                        Layout.fillWidth: true
                        implicitHeight: 32
                        radius: Theme.radiusMd
                        color: selected ? Theme.accentSubtle : (secHover.hovered ? Theme.bgHover : "transparent")

                        HoverHandler { id: secHover }
                        TapHandler {
                            onTapped: {
                                root.searchFilter = ""
                                root.activeSection = secBtn.modelData.id
                            }
                        }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme.s2 + 2
                            anchors.rightMargin: Theme.s2
                            spacing: Theme.s2

                            Icon {
                                name: secBtn.modelData.icon
                                size: 15
                                color: secBtn.selected ? Theme.accent : Theme.textSecondary
                            }

                            Text {
                                Layout.fillWidth: true
                                text: secBtn.modelData.label
                                font.family: Theme.fontSans
                                font.pixelSize: Theme.textBase
                                font.weight: secBtn.selected ? Theme.weightSemiBold : Theme.weightNormal
                                color: secBtn.selected ? Theme.textPrimary : Theme.textSecondary
                                elide: Text.ElideRight
                            }
                        }
                    }
                }

                Item { Layout.fillHeight: true }
            }
        }

        // Right Settings Content Area
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // Live Search Bar
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                color: Theme.bgCanvas

                AppTextField {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.leftMargin: Theme.s6
                    anchors.rightMargin: Theme.s6
                    leadingIcon: Icons.search
                    placeholderText: qsTr("Search all settings (e.g. theme, key, baudrate, interlock, license)...")
                    clearable: true
                    text: root.searchFilter
                    onTextEdited: root.searchFilter = text
                }

                Divider {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 1
                }
            }

            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.vertical: AppScrollBar {}
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

                Item {
                    width: parent.width
                    implicitHeight: settingsCol.implicitHeight + Theme.s6 * 2

                    ColumnLayout {
                        id: settingsCol
                        anchors.top: parent.top
                        anchors.topMargin: Theme.s5
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.leftMargin: Theme.s6
                        anchors.rightMargin: Theme.s6
                        Layout.maximumWidth: 820
                        spacing: Theme.s5

                        // 1. GENERAL
                        Card {
                            visible: root.sectionVisible("general")
                            Layout.fillWidth: true
                            iconName: Icons.settings2
                            title: qsTr("General")

                            SettingRow {
                                title: qsTr("Active Embedded Project")
                                description: (Workbench.target && Workbench.target.projectDir) ? Workbench.target.projectDir : qsTr("No project directory selected")
                                keywords: "project folder directory workspace"
                                filterText: root.searchFilter

                                AppButton {
                                    buttonSize: "sm"
                                    variant: "secondary"
                                    iconName: Icons.folderOpen
                                    text: qsTr("Browse...")
                                    onClicked: projPicker.open()
                                }
                            }

                            SettingRow {
                                title: qsTr("Auto-Refresh Hardware Telemetry")
                                description: qsTr("Poll probe, RTT/UART counts, and DWARF Live Watch on a background interval")
                                keywords: "telemetry refresh poll watch rtt"
                                filterText: root.searchFilter

                                AppSwitch {
                                    checked: Settings.autoRefresh !== undefined ? Settings.autoRefresh : true
                                    onToggled: Settings.autoRefresh = checked
                                }
                            }
                        }

                        // 2. APPEARANCE
                        Card {
                            visible: root.sectionVisible("appearance")
                            Layout.fillWidth: true
                            iconName: Icons.moon
                            title: qsTr("Appearance")
                            subtitle: qsTr("Choose light, dark, or follow operating system color scheme")

                            SettingRow {
                                title: qsTr("Color Theme")
                                description: qsTr("Applies immediately across all panels, code blocks, and dialogs")
                                keywords: "theme dark light system appearance"
                                filterText: root.searchFilter

                                RowLayout {
                                    spacing: Theme.s3

                                    Repeater {
                                        model: [
                                            { id: "system", label: qsTr("System"), bg: "#1B222E", accent: "#4C7DFF", halfLight: true },
                                            { id: "dark",   label: qsTr("Dark"),   bg: "#0B0E14", accent: "#4C7DFF", halfLight: false },
                                            { id: "light",  label: qsTr("Light"),  bg: "#F6F8FB", accent: "#2F63EB", halfLight: false }
                                        ]

                                        delegate: Rectangle {
                                            id: swatch
                                            required property var modelData
                                            readonly property bool selected: (Settings.theme || "system") === modelData.id

                                            implicitWidth: 108
                                            implicitHeight: 74
                                            radius: Theme.radiusMd
                                            color: Theme.bgElevated
                                            border.color: selected ? Theme.accent : Theme.border
                                            border.width: selected ? 2 : 1

                                            TapHandler { onTapped: Settings.setTheme(swatch.modelData.id) }

                                            ColumnLayout {
                                                anchors.fill: parent
                                                anchors.margins: Theme.s2
                                                spacing: Theme.s1 + 2

                                                Rectangle {
                                                    Layout.fillWidth: true
                                                    Layout.fillHeight: true
                                                    radius: Theme.radiusSm
                                                    color: swatch.modelData.bg
                                                    border.color: Theme.borderSubtle
                                                    border.width: 1

                                                    Rectangle {
                                                        visible: swatch.modelData.halfLight
                                                        anchors.right: parent.right
                                                        anchors.top: parent.top
                                                        anchors.bottom: parent.bottom
                                                        width: parent.width / 2
                                                        color: "#F6F8FB"
                                                    }

                                                    Rectangle {
                                                        x: 6; y: 6
                                                        width: 18; height: 6; radius: 3
                                                        color: swatch.modelData.accent
                                                    }
                                                }

                                                Text {
                                                    Layout.alignment: Qt.AlignHCenter
                                                    text: swatch.modelData.label
                                                    font.family: Theme.fontSans
                                                    font.pixelSize: Theme.textXs
                                                    font.weight: swatch.selected ? Theme.weightSemiBold : Theme.weightMedium
                                                    color: swatch.selected ? Theme.accent : Theme.textPrimary
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // 3. LANGUAGE
                        Card {
                            visible: root.sectionVisible("language")
                            Layout.fillWidth: true
                            iconName: Icons.languages
                            title: qsTr("Language")

                            SettingRow {
                                title: qsTr("Display Language")
                                description: qsTr("Switch interface text live between English and Simplified Chinese")
                                keywords: "language locale english chinese zh_CN i18n"
                                filterText: root.searchFilter

                                SegmentedControl {
                                    currentValue: Settings.language || "en"
                                    items: [
                                        { value: "system", label: qsTr("System") },
                                        { value: "en",     label: "English" },
                                        { value: "zh_CN",  label: "简体中文" }
                                    ]
                                    onActivated: function(val) { Settings.setLanguage(val) }
                                }
                            }
                        }

                        // 4. AI MODEL (9 providers)
                        Card {
                            visible: root.sectionVisible("model")
                            Layout.fillWidth: true
                            iconName: Icons.sparkles
                            title: qsTr("AI Model & Provider")
                            subtitle: qsTr("Configure LLM provider routing, API credentials, and verify latency")

                            // 9 Provider Cards
                            GridLayout {
                                Layout.fillWidth: true
                                columns: 3
                                columnSpacing: Theme.s2
                                rowSpacing: Theme.s2

                                Repeater {
                                    model: root.providers

                                    delegate: Rectangle {
                                        id: provCard
                                        required property var modelData
                                        readonly property bool selected: root.draftProvider === modelData.id

                                        Layout.fillWidth: true
                                        implicitHeight: provCol.implicitHeight + Theme.s2 * 2 + 4
                                        radius: Theme.radiusMd
                                        color: selected ? Theme.accentSubtle : (provHover.hovered ? Theme.bgHover : Theme.bgElevated)
                                        border.color: selected ? Theme.accent : Theme.borderSubtle
                                        border.width: selected ? 2 : 1

                                        HoverHandler { id: provHover }
                                        TapHandler {
                                            onTapped: {
                                                root.draftProvider = provCard.modelData.id
                                                if (root.draftModel.length === 0 || root.providers.some(function(p) { return p.defaultModel === root.draftModel })) {
                                                    root.draftModel = provCard.modelData.defaultModel
                                                }
                                            }
                                        }

                                        ColumnLayout {
                                            id: provCol
                                            anchors.fill: parent
                                            anchors.margins: Theme.s2 + 2
                                            spacing: 2

                                            RowLayout {
                                                Layout.fillWidth: true
                                                Text {
                                                    Layout.fillWidth: true
                                                    text: provCard.modelData.name
                                                    font.family: Theme.fontSans
                                                    font.pixelSize: Theme.textSm
                                                    font.weight: Theme.weightSemiBold
                                                    color: Theme.textPrimary
                                                }
                                                Icon {
                                                    visible: provCard.selected
                                                    name: Icons.circleCheck
                                                    size: 13
                                                    color: Theme.accent
                                                }
                                            }

                                            Text {
                                                Layout.fillWidth: true
                                                text: provCard.modelData.hint
                                                font.family: Theme.fontMono
                                                font.pixelSize: 10
                                                color: Theme.textMuted
                                                elide: Text.ElideRight
                                            }
                                        }
                                    }
                                }
                            }

                            SettingRow {
                                title: qsTr("Model ID")
                                description: qsTr("Leave blank to use provider default preset")
                                keywords: "model gpt claude deepseek qwen gemini ollama"
                                filterText: root.searchFilter

                                AppTextField {
                                    Layout.preferredWidth: 260
                                    monospace: true
                                    placeholderText: "deepseek-chat"
                                    text: root.draftModel
                                    onTextEdited: root.draftModel = text
                                }
                            }

                            SettingRow {
                                title: qsTr("Custom Base URL (Optional)")
                                description: qsTr("Override endpoint for OpenAI-compatible proxies or local Ollama/vLLM")
                                keywords: "base url endpoint proxy ollama vllm"
                                filterText: root.searchFilter

                                AppTextField {
                                    Layout.preferredWidth: 260
                                    monospace: true
                                    placeholderText: "https://api.openai.com/v1"
                                    text: root.draftBaseUrl
                                    onTextEdited: root.draftBaseUrl = text
                                }
                            }

                            SettingRow {
                                title: qsTr("API Key")
                                description: qsTr("Stored locally in OS keychain / 0600 config when 'Remember key' is enabled")
                                keywords: "api key token secret credentials"
                                filterText: root.searchFilter

                                ColumnLayout {
                                    spacing: Theme.s2

                                    AppTextField {
                                        Layout.preferredWidth: 260
                                        passwordField: true
                                        monospace: true
                                        placeholderText: "sk-..."
                                        text: root.draftApiKey
                                        onTextEdited: root.draftApiKey = text
                                    }

                                    AppCheckBox {
                                        text: qsTr("Remember API key securely")
                                        checked: root.draftRememberKey
                                        onToggled: root.draftRememberKey = checked
                                    }
                                }
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2

                                AppButton {
                                    variant: "secondary"
                                    iconName: Icons.zap
                                    loading: !!Settings.testingConnection
                                    text: qsTr("Test Connection")
                                    onClicked: Workbench.testModelConnection(root.draftProvider, root.draftModel, root.draftBaseUrl, root.draftApiKey)
                                }

                                Badge {
                                    visible: !!(Settings.connectionTestResult && Settings.connectionTestResult.length > 0)
                                    text: Settings.connectionTestResult || ""
                                    variant: (Settings.connectionTestOk === true) ? "success" : "danger"
                                }

                                Item { Layout.fillWidth: true }

                                AppButton {
                                    variant: "primary"
                                    iconName: Icons.check
                                    text: qsTr("Apply Model Settings")
                                    onClicked: {
                                        Workbench.applyModelConfig(root.draftProvider, root.draftModel, root.draftBaseUrl, root.draftApiKey, root.draftRememberKey)
                                        root.openToast(qsTr("Model configuration applied"), "success")
                                    }
                                }
                            }
                        }

                        // 5. HARDWARE
                        Card {
                            visible: root.sectionVisible("hardware")
                            Layout.fillWidth: true
                            iconName: Icons.hardware
                            title: qsTr("Hardware Defaults")

                            SettingRow {
                                title: qsTr("Default Probe Backend")
                                description: qsTr("Preferred hardware debug probe driver when connecting")
                                keywords: "probe jlink openocd pyocd probe-rs"
                                filterText: root.searchFilter

                                AppComboBox {
                                    model: ["jlink", "openocd", "probe-rs", "pyocd"]
                                    currentIndex: Math.max(0, ["jlink", "openocd", "probe-rs", "pyocd"].indexOf(Settings.probeBackend || "jlink"))
                                    onActivated: Settings.probeBackend = currentText
                                }
                            }

                            SettingRow {
                                title: qsTr("Default UART Baudrate")
                                description: qsTr("Serial baudrate used when starting UART capture")
                                keywords: "uart serial baudrate port"
                                filterText: root.searchFilter

                                AppComboBox {
                                    model: ["9600", "38400", "115200", "230400", "460800", "921600"]
                                    currentIndex: Math.max(0, ["9600", "38400", "115200", "230400", "460800", "921600"].indexOf(String(Settings.uartBaudrate || 115200)))
                                    onActivated: Settings.uartBaudrate = parseInt(currentText, 10)
                                }
                            }
                        }

                        // 6. SAFETY INTERLOCK
                        Card {
                            visible: root.sectionVisible("safety")
                            Layout.fillWidth: true
                            iconName: Icons.shield
                            title: qsTr("Hardware Safety Interlock")
                            subtitle: qsTr("Host-enforced HITL approval gates that cannot be bypassed by the LLM")

                            GridLayout {
                                Layout.fillWidth: true
                                columns: 3
                                columnSpacing: Theme.s3

                                Repeater {
                                    model: [
                                        { id: "interactive", icon: Icons.shieldAlert, color: Theme.warning, title: qsTr("Interactive"), desc: qsTr("Prompt before workspace writes, shell commands, probe changes, and dangerous hardware writes.") },
                                        { id: "auto_safe",   icon: Icons.shieldCheck, color: Theme.success, title: qsTr("Auto-Safe"),   desc: qsTr("Auto-allow safe reads and builds; still requires HITL approval for hardware_danger operations.") },
                                        { id: "read_only",   icon: Icons.lock,        color: Theme.info,    title: qsTr("Read-Only"),   desc: qsTr("Allow inspection only (SVD, RAM watch, logs). Blocks all writes, flashes, and resets.") }
                                    ]

                                    delegate: Rectangle {
                                        id: modeCard
                                        required property var modelData
                                        readonly property bool selected: (Workbench.interlockMode || "interactive") === modelData.id

                                        Layout.fillWidth: true
                                        implicitHeight: modeCol.implicitHeight + Theme.s3 * 2
                                        radius: Theme.radiusMd
                                        color: selected ? Theme.accentSubtle : Theme.bgElevated
                                        border.color: selected ? Theme.accent : Theme.borderSubtle
                                        border.width: selected ? 2 : 1

                                        TapHandler { onTapped: Workbench.setInterlockMode(modeCard.modelData.id) }

                                        ColumnLayout {
                                            id: modeCol
                                            anchors.fill: parent
                                            anchors.margins: Theme.s3
                                            spacing: Theme.s2

                                            RowLayout {
                                                spacing: Theme.s2
                                                Icon { name: modeCard.modelData.icon; size: 15; color: modeCard.modelData.color }
                                                Text {
                                                    text: modeCard.modelData.title
                                                    font.family: Theme.fontSans
                                                    font.pixelSize: Theme.textBase
                                                    font.weight: Theme.weightSemiBold
                                                    color: Theme.textPrimary
                                                }
                                            }

                                            Text {
                                                Layout.fillWidth: true
                                                text: modeCard.modelData.desc
                                                font.family: Theme.fontSans
                                                font.pixelSize: Theme.textXs
                                                color: Theme.textSecondary
                                                wrapMode: Text.WordWrap
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        // 7. UPDATES
                        Card {
                            visible: root.sectionVisible("updates")
                            Layout.fillWidth: true
                            iconName: Icons.download
                            title: qsTr("Software Updates")

                            SettingRow {
                                title: qsTr("Check for Updates Automatically")
                                description: qsTr("Verify signed releases over HTTPS at startup")
                                keywords: "update release version check"
                                filterText: root.searchFilter

                                AppSwitch {
                                    checked: Settings.autoCheckUpdates !== undefined ? Settings.autoCheckUpdates : true
                                    onToggled: Settings.autoCheckUpdates = checked
                                }
                            }

                            SettingRow {
                                title: qsTr("Current Version: %1").arg(AppInfo.version || "0.1.0")
                                description: Updater.updateAvailable ? qsTr("Update %1 is available").arg(Updater.latestVersion) : qsTr("You are on the latest release")
                                keywords: "update download install version"
                                filterText: root.searchFilter

                                AppButton {
                                    buttonSize: "sm"
                                    variant: "secondary"
                                    iconName: Icons.refresh
                                    text: qsTr("Check for Updates")
                                    onClicked: {
                                        Updater.checkForUpdates()
                                        root.openUpdateRequested()
                                    }
                                }
                            }
                        }

                        // 8. PRIVACY & DIAGNOSTICS
                        Card {
                            visible: root.sectionVisible("privacy")
                            Layout.fillWidth: true
                            iconName: Icons.fileDown
                            title: qsTr("Privacy & Diagnostics")

                            SettingRow {
                                title: qsTr("Anonymous Crash & Usage Telemetry")
                                description: qsTr("Off by default. API keys, source code, and firmware binaries are never collected.")
                                keywords: "privacy telemetry crash opt-in"
                                filterText: root.searchFilter

                                AppSwitch {
                                    checked: Settings.telemetryOptIn === true
                                    onToggled: Settings.telemetryOptIn = checked
                                }
                            }

                            SettingRow {
                                title: qsTr("Export Diagnostics Bundle")
                                description: qsTr("Package sanitized logs, environment info, and crash dumps into a .zip archive")
                                keywords: "diagnostics export zip logs support"
                                filterText: root.searchFilter

                                AppButton {
                                    buttonSize: "sm"
                                    variant: "secondary"
                                    iconName: Icons.fileDown
                                    text: qsTr("Export ZIP...")
                                    onClicked: diagExportDlg.open()
                                }
                            }
                        }

                        // 9. LICENSE
                        Card {
                            visible: root.sectionVisible("license")
                            Layout.fillWidth: true
                            iconName: Icons.keyRound
                            title: qsTr("License")

                            SettingRow {
                                title: qsTr("License Status")
                                description: License.status === "active"
                                    ? qsTr("Licensed to %1 (%2)").arg(License.licensedTo || "User").arg(License.tier || "Pro")
                                    : qsTr("Trial: %1 days remaining").arg(License.trialDaysRemaining || 0)
                                keywords: "license activation key trial machine id"
                                filterText: root.searchFilter

                                AppButton {
                                    buttonSize: "sm"
                                    variant: "primary"
                                    iconName: Icons.keyRound
                                    text: qsTr("Manage License")
                                    onClicked: root.openLicenseRequested()
                                }
                            }
                        }

                        // 10. ABOUT
                        Card {
                            visible: root.sectionVisible("about")
                            Layout.fillWidth: true
                            iconName: Icons.info
                            title: qsTr("About MCU Harness Studio")

                            KeyValueRow { label: qsTr("Studio Version"); value: AppInfo.version || "0.1.0"; monospace: true }
                            KeyValueRow { label: qsTr("Engine Version"); value: AppInfo.engineVersion || "0.1.0"; monospace: true }
                            KeyValueRow { label: qsTr("Git Commit");     value: AppInfo.gitHash || "dev"; monospace: true; copyable: true }

                            RowLayout {
                                Layout.fillWidth: true
                                Item { Layout.fillWidth: true }
                                AppButton {
                                    buttonSize: "sm"
                                    variant: "secondary"
                                    text: qsTr("View Third-Party Licenses & Credits")
                                    onClicked: root.openAboutRequested()
                                }
                            }
                        }

                        Item { Layout.preferredHeight: Theme.s6 }
                    }
                }
            }
        }
    }
}
