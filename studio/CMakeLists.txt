cmake_minimum_required(VERSION 3.21)

project(McuHarnessStudio VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Qt6 6.5 REQUIRED COMPONENTS
    Core
    Gui
    Quick
    QuickControls2
    Qml
    Svg
    Network
    LinguistTools
    Test
)

qt_standard_project_setup(
    REQUIRES 6.5
    I18N_SOURCE_LANGUAGE en
    I18N_TRANSLATED_LANGUAGES zh_CN
)

option(MCU_STUDIO_BUILD_TOOLS "Build studio helper tools (studio-icongen)" ON)
set(MCU_STUDIO_UPDATE_URL "" CACHE STRING "Default update manifest URL")
set(MCU_STUDIO_CRASH_URL "" CACHE STRING "Crash report upload endpoint URL")

# Git hash and UTC build date
execute_process(
    COMMAND git rev-parse --short HEAD
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    OUTPUT_VARIABLE MCU_STUDIO_GIT_HASH
    OUTPUT_STRIP_TRAILING_WHITESPACE
    ERROR_QUIET
)
if(NOT MCU_STUDIO_GIT_HASH)
    set(MCU_STUDIO_GIT_HASH "unknown")
endif()

string(TIMESTAMP MCU_STUDIO_BUILD_DATE "%Y-%m-%d" UTC)

# Read Ed25519 public keys if present; fallback to 32-byte zero base64 placeholder
set(_MCU_FALLBACK_PUBKEY_B64 "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")

set(_LICENSE_PUBKEY_FILE "${CMAKE_CURRENT_SOURCE_DIR}/keys/license_pubkey.b64")
if(EXISTS "${_LICENSE_PUBKEY_FILE}")
    file(READ "${_LICENSE_PUBKEY_FILE}" MCU_STUDIO_LICENSE_PUBKEY_B64)
    string(STRIP "${MCU_STUDIO_LICENSE_PUBKEY_B64}" MCU_STUDIO_LICENSE_PUBKEY_B64)
else()
    set(MCU_STUDIO_LICENSE_PUBKEY_B64 "${_MCU_FALLBACK_PUBKEY_B64}")
endif()

set(_UPDATE_PUBKEY_FILE "${CMAKE_CURRENT_SOURCE_DIR}/keys/update_pubkey.b64")
if(EXISTS "${_UPDATE_PUBKEY_FILE}")
    file(READ "${_UPDATE_PUBKEY_FILE}" MCU_STUDIO_UPDATE_PUBKEY_B64)
    string(STRIP "${MCU_STUDIO_UPDATE_PUBKEY_B64}" MCU_STUDIO_UPDATE_PUBKEY_B64)
else()
    set(MCU_STUDIO_UPDATE_PUBKEY_B64 "${_MCU_FALLBACK_PUBKEY_B64}")
endif()

get_filename_component(MCU_STUDIO_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
set(MCU_STUDIO_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}")
set(MCU_STUDIO_BUILD_DIR "${CMAKE_CURRENT_BINARY_DIR}")

configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/cmake/BuildConfig.h.in"
    "${CMAKE_CURRENT_BINARY_DIR}/generated/BuildConfig.h"
    @ONLY
)

# ------------------------------------------------------------------------------
# studio_core static library
# ------------------------------------------------------------------------------
set(STUDIO_CORE_CXX_SOURCES
    src/app/AppInfo.h
    src/app/AppInfo.cpp
    src/app/Application.h
    src/app/Application.cpp
    src/core/Logger.h
    src/core/Logger.cpp
    src/core/CrashHandler.h
    src/core/CrashHandler.cpp
    src/core/AppSettings.h
    src/core/AppSettings.cpp
    src/core/IconProvider.h
    src/core/IconProvider.cpp
    src/core/MachineId.h
    src/core/MachineId.cpp
    src/core/LicenseManager.h
    src/core/LicenseManager.cpp
    src/core/UpdateManager.h
    src/core/UpdateManager.cpp
    src/core/DiagnosticsService.h
    src/core/DiagnosticsService.cpp
    src/core/Utils.h
    src/core/Utils.cpp
    src/backend/EngineClient.h
    src/backend/EngineClient.cpp
    src/models/ConversationModel.h
    src/models/ConversationModel.cpp
    src/models/LogModel.h
    src/models/LogModel.cpp
    src/models/WatchModel.h
    src/models/WatchModel.cpp
    src/models/RtosTaskModel.h
    src/models/RtosTaskModel.cpp
    src/models/AuditModel.h
    src/models/AuditModel.cpp
    src/models/SessionModel.h
    src/models/SessionModel.cpp
    src/models/PlaybookModel.h
    src/models/PlaybookModel.cpp
    src/controllers/WorkbenchController.h
    src/controllers/WorkbenchController.cpp
    src/controllers/DemoSeeder.h
    src/controllers/DemoSeeder.cpp
)

set(STUDIO_CORE_C_SOURCES
    third_party/tweetnacl/tweetnacl.c
    src/core/randombytes_stub.c
)

add_library(studio_core STATIC
    ${STUDIO_CORE_CXX_SOURCES}
    ${STUDIO_CORE_C_SOURCES}
)

target_include_directories(studio_core PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}/src"
    "${CMAKE_CURRENT_SOURCE_DIR}/third_party/tweetnacl"
    "${CMAKE_CURRENT_BINARY_DIR}/generated"
)

target_link_libraries(studio_core PUBLIC
    Qt6::Core
    Qt6::Gui
    Qt6::Quick
    Qt6::QuickControls2
    Qt6::Qml
    Qt6::Svg
    Qt6::Network
)

if(MSVC)
    target_compile_options(studio_core PRIVATE $<$<COMPILE_LANGUAGE:CXX>:/W4>)
else()
    target_compile_options(studio_core PRIVATE $<$<COMPILE_LANGUAGE:CXX>:-Wall;-Wextra>)
endif()

if(WIN32)
    target_link_libraries(studio_core PRIVATE Dbghelp)
endif()

# ------------------------------------------------------------------------------
# Optional build tools (studio-icongen)
# ------------------------------------------------------------------------------
if(MCU_STUDIO_BUILD_TOOLS)
    qt_add_executable(studio-icongen tools/icongen.cpp)
    target_link_libraries(studio-icongen PRIVATE Qt6::Core Qt6::Gui Qt6::Svg)
    if(MSVC)
        target_compile_options(studio-icongen PRIVATE /W4)
    else()
        target_compile_options(studio-icongen PRIVATE -Wall -Wextra)
    endif()

    set(_STUDIO_ICON_SVG "${CMAKE_CURRENT_SOURCE_DIR}/resources/app/mcu-harness-studio.svg")
    set(_STUDIO_ICON_OUT "${CMAKE_CURRENT_BINARY_DIR}/icons")
    set(_STUDIO_ICON_OUTPUTS "${_STUDIO_ICON_OUT}/mcu-harness-studio.ico")
    foreach(_sz 16 24 32 48 64 128 256 512)
        list(APPEND _STUDIO_ICON_OUTPUTS "${_STUDIO_ICON_OUT}/mcu-harness-studio-${_sz}.png")
    endforeach()

    if(EXISTS "${_STUDIO_ICON_SVG}")
        add_custom_command(
            OUTPUT ${_STUDIO_ICON_OUTPUTS}
            COMMAND $<TARGET_FILE:studio-icongen> "${_STUDIO_ICON_SVG}" "${_STUDIO_ICON_OUT}"
            DEPENDS studio-icongen "${_STUDIO_ICON_SVG}"
            COMMENT "Rendering multi-resolution PNG icons and Windows .ico"
            VERBATIM
        )
        add_custom_target(studio_icons ALL DEPENDS ${_STUDIO_ICON_OUTPUTS})
    endif()
endif()

# ------------------------------------------------------------------------------
# Main executable + QML module
# ------------------------------------------------------------------------------
qt_add_executable(mcu-harness-studio WIN32 MACOSX_BUNDLE
    src/main.cpp
)

target_link_libraries(mcu-harness-studio PRIVATE studio_core)

if(MSVC)
    target_compile_options(mcu-harness-studio PRIVATE /W4)
else()
    target_compile_options(mcu-harness-studio PRIVATE -Wall -Wextra)
endif()

set_source_files_properties(
    qml/theme/Theme.qml
    qml/theme/Icons.qml
    qml/theme/PluginRegistry.qml
    PROPERTIES QT_QML_SINGLETON_TYPE TRUE
)

set(STUDIO_QML_FILES
    qml/Main.qml
    qml/theme/Theme.qml
    qml/theme/Icons.qml
    qml/theme/PluginRegistry.qml
    qml/components/AppButton.qml
    qml/components/IconButton.qml
    qml/components/AppTextField.qml
    qml/components/AppTextArea.qml
    qml/components/AppComboBox.qml
    qml/components/AppSwitch.qml
    qml/components/AppCheckBox.qml
    qml/components/SegmentedControl.qml
    qml/components/TabBarLine.qml
    qml/components/Badge.qml
    qml/components/StatusDot.qml
    qml/components/Card.qml
    qml/components/Divider.qml
    qml/components/AppToolTip.qml
    qml/components/Kbd.qml
    qml/components/Icon.qml
    qml/components/Spinner.qml
    qml/components/AppProgressBar.qml
    qml/components/EmptyState.qml
    qml/components/SectionHeader.qml
    qml/components/AppScrollBar.qml
    qml/components/AppDialog.qml
    qml/components/Toast.qml
    qml/components/ToastHost.qml
    qml/components/CodeBlock.qml
    qml/components/DiffView.qml
    qml/components/JsonView.qml
    qml/components/MarkdownText.qml
    qml/components/KeyValueRow.qml
    qml/components/SettingRow.qml
    qml/components/GradientButton.qml
    qml/components/IconChip.qml
    qml/components/Pill.qml
    qml/components/Chip.qml
    qml/components/AuroraBackdrop.qml
    qml/components/AgentOrb.qml
    qml/components/TypingDots.qml
    qml/components/PanelHeader.qml
    qml/components/SearchField.qml
    qml/components/ResizeHandle.qml
    qml/shell/NavRail.qml
    qml/shell/TopBar.qml
    qml/shell/ThreadsPanel.qml
    qml/shell/PluginBar.qml
    qml/shell/PluginPanel.qml
    qml/shell/StatusBar.qml
    qml/shell/ScreenshotDriver.qml
    qml/pages/ChatPage.qml
    qml/pages/SkillsPage.qml
    qml/pages/PluginsPage.qml
    qml/pages/HistoryPage.qml
    qml/pages/SettingsPage.qml
    qml/chat/UserBubble.qml
    qml/chat/ThinkingBlock.qml
    qml/chat/ToolStep.qml
    qml/chat/AnswerBlock.qml
    qml/chat/NoticeRow.qml
    qml/chat/ActivityPill.qml
    qml/chat/Composer.qml
    qml/chat/ChatEmptyState.qml
    qml/chat/SkillPicker.qml
    qml/plugins/ProbePlugin.qml
    qml/plugins/WatchPlugin.qml
    qml/plugins/RtosPlugin.qml
    qml/plugins/ConsolePlugin.qml
    qml/plugins/AuditPlugin.qml
    qml/plugins/EngineLogPlugin.qml
    qml/dialogs/CommandPalette.qml
    qml/dialogs/ApprovalDialog.qml
    qml/dialogs/OnboardingWizard.qml
    qml/dialogs/LicenseDialog.qml
    qml/dialogs/UpdateDialog.qml
    qml/dialogs/CrashReportDialog.qml
    qml/dialogs/AboutDialog.qml
)

file(GLOB STUDIO_ICON_SVGS CONFIGURE_DEPENDS RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}" "resources/icons/*.svg")
file(GLOB STUDIO_FONT_TTFS CONFIGURE_DEPENDS RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}" "resources/fonts/*.ttf")
file(GLOB STUDIO_FONT_TXTS CONFIGURE_DEPENDS RELATIVE "${CMAKE_CURRENT_SOURCE_DIR}" "resources/fonts/*.txt")

set(STUDIO_RESOURCES
    ${STUDIO_ICON_SVGS}
    ${STUDIO_FONT_TTFS}
    ${STUDIO_FONT_TXTS}
)
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/resources/icons/LUCIDE-LICENSE.txt")
    list(APPEND STUDIO_RESOURCES "resources/icons/LUCIDE-LICENSE.txt")
endif()
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/resources/app/mcu-harness-studio.svg")
    list(APPEND STUDIO_RESOURCES "resources/app/mcu-harness-studio.svg")
endif()

qt_add_qml_module(mcu-harness-studio
    URI McuStudio
    VERSION 1.0
    QML_FILES ${STUDIO_QML_FILES}
    RESOURCES ${STUDIO_RESOURCES}
)

if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/translations/mcu_studio_zh_CN.ts")
    qt_add_translations(
        TARGETS mcu-harness-studio
        SOURCE_TARGETS studio_core mcu-harness-studio
        TS_FILES translations/mcu_studio_zh_CN.ts
        LUPDATE_OPTIONS -extensions cpp,h,qml -no-obsolete
        RESOURCE_PREFIX "/i18n"
    )
endif()

# ------------------------------------------------------------------------------
# Tests (QtTest + CTest)
# ------------------------------------------------------------------------------
enable_testing()

set(_MCU_TEST_DEFS
    MCU_TEST_DATA_DIR="${CMAKE_CURRENT_SOURCE_DIR}/tests/data"
    MCU_STUDIO_SOURCE_DIR="${CMAKE_CURRENT_SOURCE_DIR}"
    MCU_STUDIO_BUILD_DIR="${CMAKE_CURRENT_BINARY_DIR}"
    MCU_REPO_ROOT="${MCU_STUDIO_REPO_ROOT}"
)

function(mcu_add_studio_test test_name test_src)
    qt_add_executable(${test_name} ${test_src} ${ARGN})
    target_link_libraries(${test_name} PRIVATE studio_core Qt6::Test)
    target_compile_definitions(${test_name} PRIVATE ${_MCU_TEST_DEFS})
    if(MSVC)
        target_compile_options(${test_name} PRIVATE /W4)
    else()
        target_compile_options(${test_name} PRIVATE -Wall -Wextra)
    endif()
    add_test(NAME ${test_name} COMMAND ${test_name})
    set_tests_properties(${test_name} PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen;QT_QUICK_BACKEND=software")
endfunction()

mcu_add_studio_test(tst_engineclient tests/tst_engineclient.cpp)
mcu_add_studio_test(tst_license tests/tst_license.cpp)
mcu_add_studio_test(tst_update tests/tst_update.cpp)
mcu_add_studio_test(tst_models tests/tst_models.cpp)

qt6_add_resources(TST_QML_SMOKE_RCC
    "${CMAKE_CURRENT_BINARY_DIR}/.qt/rcc/qmake_McuStudio.qrc"
    "${CMAKE_CURRENT_BINARY_DIR}/.qt/rcc/mcu-harness-studio_raw_qml_0.qrc"
    "${CMAKE_CURRENT_BINARY_DIR}/.qt/rcc/mcu-harness-studio_raw_res_0.qrc"
)
mcu_add_studio_test(tst_qml_smoke tests/tst_qml_smoke.cpp ${TST_QML_SMOKE_RCC})
add_dependencies(tst_qml_smoke mcu-harness-studio)

# ------------------------------------------------------------------------------
# Install & CPack Packaging
# ------------------------------------------------------------------------------
include(GNUInstallDirs)

install(TARGETS mcu-harness-studio
    BUNDLE DESTINATION .
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
)

install(DIRECTORY "${MCU_STUDIO_REPO_ROOT}/src/mcu_harness"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/mcu-harness-studio/python"
    PATTERN "__pycache__" EXCLUDE
    PATTERN "*.pyc" EXCLUDE
    PATTERN "*.pyo" EXCLUDE
)

if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/resources/app/mcu-harness-studio.svg")
    install(FILES resources/app/mcu-harness-studio.svg
        DESTINATION "${CMAKE_INSTALL_DATADIR}/icons/hicolor/scalable/apps"
    )
endif()

if(MCU_STUDIO_BUILD_TOOLS AND EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/resources/app/mcu-harness-studio.svg")
    foreach(_sz 16 24 32 48 64 128 256 512)
        install(FILES "${CMAKE_CURRENT_BINARY_DIR}/icons/mcu-harness-studio-${_sz}.png"
            DESTINATION "${CMAKE_INSTALL_DATADIR}/icons/hicolor/${_sz}x${_sz}/apps"
            RENAME "mcu-harness-studio.png"
        )
    endforeach()
endif()

if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/packaging/linux/mcu-harness-studio.desktop")
    install(FILES packaging/linux/mcu-harness-studio.desktop
        DESTINATION "${CMAKE_INSTALL_DATADIR}/applications"
    )
endif()

if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/packaging/linux/dev.mcuharness.Studio.metainfo.xml")
    install(FILES packaging/linux/dev.mcuharness.Studio.metainfo.xml
        DESTINATION "${CMAKE_INSTALL_DATADIR}/metainfo"
    )
endif()

install(FILES
    resources/fonts/Inter-OFL.txt
    resources/fonts/JetBrainsMono-OFL.txt
    resources/icons/LUCIDE-LICENSE.txt
    DESTINATION "${CMAKE_INSTALL_DOCDIR}/licenses"
)

set(CPACK_PACKAGE_NAME "mcu-harness-studio")
set(CPACK_PACKAGE_VENDOR "Lannuo")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_PACKAGE_DIRECTORY "${MCU_STUDIO_REPO_ROOT}/dist")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "MCU Harness Studio — Embedded AI & Hardware Debugging Workbench")
set(CPACK_PACKAGE_CONTACT "Lannuo <support@mcuharness.dev>")
set(CPACK_GENERATOR "DEB")
set(CPACK_DEBIAN_PACKAGE_MAINTAINER "${CPACK_PACKAGE_CONTACT}")
set(CPACK_DEBIAN_PACKAGE_SECTION "devel")
set(CPACK_DEBIAN_FILE_NAME "mcu-harness-studio_${PROJECT_VERSION}_amd64.deb")
set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)
set(CPACK_DEBIAN_PACKAGE_DEPENDS
    "python3 (>= 3.10), qml6-module-qtquick, qml6-module-qtquick-controls, qml6-module-qtquick-layouts, qml6-module-qtquick-effects, qml6-module-qtquick-dialogs, qml6-module-qtquick-templates, qml6-module-qtquick-window, qml6-module-qtqml-workerscript, qml6-module-qtquick-shapes, qt6-svg-plugins"
)
include(CPack)
