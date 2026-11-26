import QtQuick
import McuStudio

QtObject {
    id: driver

    // Set by Main.qml Loader bindings
    property var mainWindow: null

    function closeAllOverlays() {
        if (!mainWindow) return
        if (mainWindow.commandPalette) mainWindow.commandPalette.close()
        if (mainWindow.approvalDialog) mainWindow.approvalDialog.close()
        if (mainWindow.onboardingWizard) mainWindow.onboardingWizard.close()
        if (mainWindow.licenseDialog) mainWindow.licenseDialog.close()
        if (mainWindow.updateDialog) mainWindow.updateDialog.close()
        if (mainWindow.crashReportDialog) mainWindow.crashReportDialog.close()
        if (mainWindow.aboutDialog) mainWindow.aboutDialog.close()
    }

    function gotoState(state) {
        if (!mainWindow) return
        closeAllOverlays()

        if (typeof Workbench.seedDemoState === "function") {
            Workbench.seedDemoState(state)
        }

        switch (state) {
        case "agent-empty":
            mainWindow.activePage = 0
            mainWindow.sidebarVisible = true
            mainWindow.bottomPanelVisible = false
            break
        case "agent-conversation":
            mainWindow.activePage = 0
            mainWindow.sidebarVisible = true
            mainWindow.bottomPanelVisible = true
            break
        case "approval-danger":
            mainWindow.activePage = 0
            if (mainWindow.approvalDialog) {
                mainWindow.approvalDialog.demoOverride = {
                    requestId: "demo-danger-1",
                    toolName: "flash_firmware",
                    riskLevel: "hardware_danger",
                    summary: "Erase sectors 0–7 and program build/stm32h563_motor.elf (184 KB) via J-Link SWD",
                    detailsJson: JSON.stringify({
                        elf_path: "build/stm32h563_motor.elf",
                        device: "STM32H563VIT6",
                        interface: "SWD",
                        speed_khz: 4000,
                        verify: true
                    }, null, 2),
                    diffPreview: "",
                    svdPreview: "FLASH->CR = 0x00000002 (PG=1, SER=0, MER=0)\n  [1] PG = 1 (Programming)\n  [2] PER = 0 (Sector Erase)",
                    isHardwareDanger: true
                }
                mainWindow.approvalDialog.open()
            }
            break
        case "approval-diff":
            mainWindow.activePage = 0
            if (mainWindow.approvalDialog) {
                mainWindow.approvalDialog.demoOverride = {
                    requestId: "demo-diff-1",
                    toolName: "edit_file",
                    riskLevel: "workspace_write",
                    summary: "Guard DMA Rx pointer against NULL before HAL_UART_Receive_DMA in Core/Src/usart.c",
                    detailsJson: JSON.stringify({
                        path: "Core/Src/usart.c",
                        line_start: 142,
                        line_end: 150
                    }, null, 2),
                    diffPreview: "--- a/Core/Src/usart.c\n+++ b/Core/Src/usart.c\n@@ -142,6 +142,9 @@ void Uart_StartRx(uint8_t *buf, uint16_t len)\n {\n-  HAL_UART_Receive_DMA(&huart3, buf, len);\n+  if (buf == NULL || len == 0U) {\n+    return;\n+  }\n+  HAL_UART_Receive_DMA(&huart3, buf, len);\n }",
                    svdPreview: "",
                    isHardwareDanger: false
                }
                mainWindow.approvalDialog.open()
            }
            break
        case "hardware":
            mainWindow.activePage = 1
            mainWindow.sidebarVisible = true
            mainWindow.bottomPanelVisible = false
            break
        case "watch":
            mainWindow.activePage = 2
            mainWindow.sidebarVisible = true
            mainWindow.bottomPanelVisible = false
            break
        case "rtos":
            mainWindow.activePage = 3
            mainWindow.sidebarVisible = true
            mainWindow.bottomPanelVisible = false
            break
        case "sessions":
            mainWindow.activePage = 4
            mainWindow.sidebarVisible = true
            mainWindow.bottomPanelVisible = false
            break
        case "audit":
            mainWindow.activePage = 5
            mainWindow.sidebarVisible = true
            mainWindow.bottomPanelVisible = false
            break
        case "settings-model":
            mainWindow.activePage = 6
            if (mainWindow.settingsPage) mainWindow.settingsPage.activeSection = "model"
            break
        case "settings-appearance":
            mainWindow.activePage = 6
            if (mainWindow.settingsPage) mainWindow.settingsPage.activeSection = "appearance"
            break
        case "license":
            mainWindow.activePage = 0
            if (mainWindow.licenseDialog) mainWindow.licenseDialog.open()
            break
        case "update-available":
            mainWindow.activePage = 0
            if (mainWindow.updateDialog) mainWindow.updateDialog.open()
            break
        case "onboarding-1":
            mainWindow.activePage = 0
            if (mainWindow.onboardingWizard) {
                mainWindow.onboardingWizard.stepIndex = 0
                mainWindow.onboardingWizard.open()
            }
            break
        case "onboarding-3":
            mainWindow.activePage = 0
            if (mainWindow.onboardingWizard) {
                mainWindow.onboardingWizard.stepIndex = 2
                mainWindow.onboardingWizard.open()
            }
            break
        case "palette":
            mainWindow.activePage = 0
            if (mainWindow.commandPalette) {
                mainWindow.commandPalette.openPalette()
            }
            break
        case "crash":
            mainWindow.activePage = 0
            if (mainWindow.crashReportDialog) {
                mainWindow.crashReportDialog.open()
            }
            break
        }
    }
}
