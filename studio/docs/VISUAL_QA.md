# MCU Harness Studio v2 "Aurora" — Visual QA Report

## Summary & Verification Counts

- **Screenshot Matrix**: **88 PNGs** (`22 states × 2 themes (dark, light) × 2 locales (en, zh_CN)`) captured via `QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software ./build/release/mcu-harness-studio --screenshot build/screenshots`
- **QML Runtime Warnings**: **0** (`grep -c "qml:" /tmp/shots.log` = `0`)
- **Localization (`zh_CN`)**: **725 / 725** finished entries (`0` unfinished, `0` obsolete/vanished)
- **QtTest C++20 / QML Suite**: **5 / 5 passed** (`tst_engineclient`, `tst_license`, `tst_update`, `tst_models`, `tst_qml_smoke`)
- **Python `unittest` Suite**: **117 / 117 passed** across all 17 unittest modules (in 32.200s)

## Software-Renderer Notes

- `Theme.effectsEnabled` disables `MultiEffect` blur/drop-shadow when `GraphicsInfo.api === GraphicsInfo.Software` or `Qt.platform.pluginName === "offscreen"`, falling back to crisp 1px `borderStrong` outlines and multi-stop radial/layered rectangles (`AuroraBackdrop.qml`, `AppDialog.qml`, `AppComboBox.qml`, `CommandPalette.qml`) so no opaque-box or alpha artifacts occur.
- `Theme.animationsEnabled` resolves to `!AppInfo.screenshotMode`, setting `Behavior` and page/list transition durations to `0` ms during `--screenshot` runs so captures never land mid-transition.
- Gradients use native `Rectangle.gradient` and Qt 6.11 per-corner radii (`UserBubble.qml`), which render identically in software and GPU backends.

## 22-State Matrix Coverage

| # | State ID | Page / Shell Layout | Visual & Interactive Elements Verified |
| --- | --- | --- | --- |
| 1 | `agent-empty` | Chat (page 0), ThreadsPanel open, PluginPanel closed | `AuroraBackdrop`, 64px `AgentOrb`, `"What are we debugging today?"` hero, 2×2 colored skill cards, `Composer` |
| 2 | `agent-conversation` | Chat (page 0), ThreadsPanel open, PluginPanel closed | User bubble, collapsed/finished `ThinkingBlock` (`4.2s`, `1.8s`), `ToolStep` cards (`diagnose_crash`, `inspect_board`), markdown register table + steps |
| 3 | `agent-streaming` | Chat (page 0), `agentPhase="thinking"` | Streaming expanded `ThinkingBlock` with violet `brain` icon + `TypingDots`, `ActivityPill` (`Thinking…`, `Step 1`, `Esc` Stop button) |
| 4 | `plugin-probe` | Chat + right `PluginPanel` (`probe`) | `PanelHeader` in `hueAmber`, backend selector, probe connect/halt/resume/reset controls, target & ELF/SVD/IOC chips, `PluginBar` amber active bar |
| 5 | `plugin-watch` | Chat + right `PluginPanel` (`watch`) | `PanelHeader` in `hueCyan`, DWARF expression toolbar, watch cards with delta highlight and mini trend `Canvas` |
| 6 | `plugin-rtos` | Chat + right `PluginPanel` (`rtos`) | `PanelHeader` in `hueEmerald`, RTOS hazard alert cards, task cards with emerald/amber/rose stack watermark bars |
| 7 | `plugin-console` | Chat + right `PluginPanel` (`console`) | `PanelHeader` in `hueSky`, RTT / UART tabs, port & baudrate controls, TX command input, colored log stream |
| 8 | `plugin-audit` | Chat + right `PluginPanel` (`audit`) | `PanelHeader` in `hueRose`, verdict/scope filter chips, `SearchField`, risk badges, sanitized `.zip` diagnostics export |
| 9 | `plugin-maximized` | Page 5 (full-width maximized `watch` plugin) | `NavRail` active indicator hidden, full-width 2-column responsive `WatchPlugin` layout, `PanelHeader` restore button |
| 10 | `skills` | Skills (page 1) | Gradient banner header, `SearchField`, colored skill grid with `"Run in chat"` `GradientButton`, quick command `Chip`s |
| 11 | `plugins` | Plugins (page 2) | Gradient banner header, category filter chips, built-in plugin cards (8px hue gradient top strip, `AppSwitch`, `"Open"`), `"Coming Soon"` dimmed cards |
| 12 | `history` | History (page 3) | Searchable Aurora session rows, `"Refresh"` + `"New chat"` `GradientButton`, active gradient marker, `"Resume"` action |
| 13 | `approval-danger` | Chat + `ApprovalDialog` (`hardware_danger`) | Rose-to-orange danger header strip, `shield-alert` icon, CMSIS-SVD bitfield diff preview, `"Allow for session"` permanently hidden |
| 14 | `approval-diff` | Chat + `ApprovalDialog` (`workspace_write`) | 2px brand-gradient top hairline, Unified Diff preview (`DiffView`), once / session / deny buttons |
| 15 | `settings-model` | Settings (page 4), section `model` | Rail-style left nav, LLM provider/model/base URL/API key form, Test Connection + Apply controls |
| 16 | `settings-appearance` | Settings (page 4), section `appearance` | Theme/language/font size selectors, **Thinking Chain** (`Settings.showThinking`) toggle, **Enabled Plugins** toggles |
| 17 | `license` | Chat + `LicenseDialog` | Evaluation trial / active license status badge, trial progress bar, machine ID copy, offline license import |
| 18 | `update-available` | Chat + `UpdateDialog` | New version banner, release notes, Ed25519 signature status, download/install progress |
| 19 | `onboarding-1` | Chat + `OnboardingWizard` (step 1) | Language/theme selection step, step indicator pills |
| 20 | `onboarding-3` | Chat + `OnboardingWizard` (step 3) | Equalized hardware backend & completion cards |
| 21 | `palette` | Chat + `CommandPalette` (`Ctrl+K`) | 640px centered palette, `SearchField`, grouped `Plugins`, `Skills`, `Pages`, and `Actions` with colored `IconChip`s |
| 22 | `crash` | Chat + `CrashReportDialog` | Crash timestamp/signal summary, captured stack frames, export diagnostics `.zip` / dismiss / send actions |
