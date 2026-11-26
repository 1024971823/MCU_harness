# MCU Harness Studio Visual QA Report

## Scope & Method

Automated offscreen screenshot verification across the full UI matrix (`QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software`, `1440x900`), covering **2 languages** (`zh_CN`, `en`) × **2 themes** (`dark`, `light`) × **17 states** = **68 PNG renders** per pass, paired with manual inspection of all `zh_CN` dark and light renders and `tst_qml_smoke` (`0` QML warnings/errors gate).

---

## Pass 1 — Findings & Root Causes

1. **Transparent Modal & Command Palette Backgrounds (Critical)**
   - **Affected states**: `approval-danger`, `approval-diff`, `license`, `update-available`, `onboarding-1`, `onboarding-3`, `palette`, `crash` (both dark & light).
   - **Symptom**: Inner cards and footer controls rendered cleanly, while the outer `AppDialog` / `CommandPalette` surface was completely transparent, showing the dimmed workspace underneath.
   - **Root cause**: `CMakeLists.txt` and `tst_qml_smoke.cpp` set `QSG_RHI_BACKEND=software` (instead of `QT_QUICK_BACKEND=software`), so `GraphicsInfo.api !== GraphicsInfo.Software` evaluated `true` under `QT_QPA_PLATFORM=offscreen`. Inline `layer.effect: MultiEffect` on the dialog `background` `Rectangle` allocated an offscreen shader layer that rendered empty under software offscreen composition.
   - **Fix**: Updated `Theme.qml` (`effectsEnabled: GraphicsInfo.api !== GraphicsInfo.Software && Qt.platform.pluginName !== "offscreen"`), moved `MultiEffect` into lazy `Component` instances bound only when `Theme.effectsEnabled` is true (`layer.effect: Theme.effectsEnabled ? shadowComp : null`), and switched `CMakeLists.txt` / `tst_qml_smoke.cpp` to `QT_QUICK_BACKEND=software`.

2. **Empty State Missing Titles & Composer Overlap (Critical)**
   - **Affected states**: `agent-empty` (dark & light) + `tst_qml_smoke`.
   - **Symptom**: Suggestion cards 4 & 5 lacked titles (`Unable to assign [undefined] to QString`) and the bottom row of cards extended slightly behind the composer bar due to `ScrollView` content height binding.
   - **Fix**: Added `cmd` and `title` properties to all 6 entries in `AgentPage.qml::suggestions`, widened subtitle max width to prevent single-word widow wrapping, and replaced the `ScrollView > Item` centering block with an explicit centered `Flickable` layout.

3. **Mixed English Strings in `zh_CN` UI**
   - **Affected states**: `SideBar` playbooks, `SessionsPage` relative timestamps, `StatusBar` interlock badge, `UpdateDialog` status pill, `ApprovalDialog` risk badge.
   - **Root cause**: Initial `.ts` extraction covered `qml/` only, missing C++ `tr()` strings in `PlaybookModel` and `SessionModel`, and several status pills displayed raw enum identifiers.
   - **Fix**: Configured `qt_add_translations` with `SOURCE_TARGETS studio_core mcu-harness-studio` and `LUPDATE_OPTIONS -extensions cpp,h,qml`; added live `languageChanged` refresh hooks for `PlaybookModel` and `SessionModel`; localized risk, interlock, and updater status badges; completed all **514/514** translations in `studio/translations/mcu_studio_zh_CN.ts` (`0` unfinished).

4. **Default Provider & Onboarding Column Alignment**
   - **Affected states**: `settings-model`, `onboarding-3`.
   - **Symptom**: `AppSettings` defaulted to `"openai"` while `Model ID` placeholder showed `deepseek-chat`; in Onboarding Step 3, `Provider` combo took ~75% width squeezing `Model ID`.
   - **Fix**: Aligned `AppSettings` default provider to `"deepseek"`, removed `agent/apiKey` persistence from `QSettings` (keys are stored only in engine `agent.toml` when "Remember key" is checked), and set equal `Layout.preferredWidth: 1` weights in `OnboardingWizard.qml`.

---

## Pass 2 — Verification Results

- **Compiler**: `cmake --build --preset release` — **0 warnings, 0 errors**.
- **QtTest (`ctest --preset release`)**: **5 / 5 passed** (`tst_engineclient`, `tst_license`, `tst_update`, `tst_models`, `tst_qml_smoke` with **0 QML warnings**).
- **Translation Gate**: **514 / 514 finished, 0 unfinished** (`grep -c 'type="unfinished"' studio/translations/mcu_studio_zh_CN.ts` = `0`).
- **Visual Re-inspection**: Verified regenerated `zh_CN` dark and light renders across all dialogs, empty states, settings, sessions, RTOS, watch, audit, and hardware pages:
  - Opaque elevated surfaces and crisp 1px borders confirmed across every modal dialog and command palette.
  - All 6 agent quick-start cards display icons, titles, and balanced vertical centering above the composer.
  - Zero CJK glyph boxes, zero clipped labels, consistent 4px grid alignment, and full `zh_CN` localization.
