# MCU Harness Studio v2 "Aurora" — Fixed Architecture Contracts

Every module and subagent MUST conform strictly to these contracts. Do not alter property names, signal signatures, RPC event names, or QML APIs defined here.

---

## 1.1 Python Engine Events (`src/mcu_harness/agent/`)
- New runtime event: `reasoning_delta` with payload `{"step": int, "delta": str}`, emitted before `text_delta` within a step. RPC (`rpc.py`) forwards all runtime events generically, so no change is needed in `rpc.py`.
- `LlmResponse` gains:
  - `reasoning: str = ""`
  - `reasoning_blocks: list[dict] = field(default_factory=list)` (Anthropic signed thinking blocks)
  - Include both `reasoning` and `reasoning_blocks` in `LlmResponse.to_dict()`.
- `LlmProvider.complete(..., on_reasoning_delta: Callable[[str], None] | None = None)`.
- `AgentRuntime` records `assistant_msg["reasoning"]` (when non-empty or present) and, if non-empty, `assistant_msg["reasoning_blocks"]` on the assistant message dict in `self.messages` / session JSONL.

---

## 1.2 C++ Core (`studio_core`)

### `ConversationModel` (`studio/src/models/ConversationModel.{h,cpp}`)
- Supports entry `kind` values: `"user"`, `"thinking"`, `"assistant"`, `"tool_call"`, `"tool_result"`, `"notice"`.
- New roles (exposed via `roleNames()`):
  - `startedMs` (`qint64`) — `StartedMsRole`
  - `expanded` (`bool`) — `ExpandedRole`
  - `groupStart` (`bool`) — `GroupStartRole`
  - `groupEnd` (`bool`) — `GroupEndRole`
- Computed grouping roles in `data()`:
  - `groupStart` is `true` when the entry is NOT `"user"` or `"notice"` AND (`row == 0` OR previous row's `kind` is `"user"` or `"notice"`).
  - `groupEnd` is `true` when the entry is NOT `"user"` or `"notice"` AND (`row == count - 1` OR next row's `kind` is `"user"` or `"notice"`).
  - On every row insert, emit `dataChanged` on the previous row (if any) for `{GroupEndRole}`.
- Methods:
  - `void appendReasoningDelta(const QString &delta)`: finishes any streaming assistant text first, then appends to the current streaming `"thinking"` entry or creates one (`streaming=true`, `expanded=true`, `startedMs=QDateTime::currentMSecsSinceEpoch()`).
  - `void finishReasoning()`: sets `streaming=false`, `durationMs = max(0, now - startedMs)`, and sets `expanded=false` UNLESS `userToggled == true`. Emits `dataChanged` for `{StreamingRole, DurationMsRole, ExpandedRole}`.
  - `Q_INVOKABLE void setExpanded(int row, bool expanded)`: updates `expanded` on the row, sets internal `userToggled = true`, emits `dataChanged` for `{ExpandedRole}`.
  - `Q_INVOKABLE QString plainTextAt(int row) const`: returns entry plain text (`text`, or summary/JSON fallback for tool entries).
- `appendAssistantDelta`, `addToolCall`, `finishAssistantMessage`, and `addNotice` MUST call `finishReasoning()` first.
- `loadFromSessionMessages` creates a finished, collapsed (`streaming=false`, `expanded=false`) `"thinking"` entry immediately before the `"assistant"` content/tool entries when `msg.reasoning` is non-empty.

### `WorkbenchController` (`studio/src/controllers/WorkbenchController.{h,cpp}`)
- New properties (all with `NOTIFY agentPhaseChanged`):
  - `Q_PROPERTY(QString agentPhase READ agentPhase NOTIFY agentPhaseChanged)` — values: `"idle"`, `"waiting"`, `"thinking"`, `"responding"`, `"tool"`, `"approval"`
  - `Q_PROPERTY(qint64 turnStartedMs READ turnStartedMs NOTIFY agentPhaseChanged)`
  - `Q_PROPERTY(int currentStep READ currentStep NOTIFY agentPhaseChanged)`
  - `Q_PROPERTY(QString currentToolTitle READ currentToolTitle NOTIFY agentPhaseChanged)`
- New invokable / demo helpers:
  - `Q_INVOKABLE void retryLastTurn()` — resends the last user prompt stored in `m_lastUserText` (recorded in `sendMessage()`).
  - `void setAgentPhaseDemo(const QString &phase)` — sets `m_agentPhase = phase`, `m_turnRunning = (phase != "idle")`, and `m_turnStartedMs = now - 6400` when running (emits `agentPhaseChanged()` and `turnRunningChanged()` as needed).

| Event | Action |
|---|---|
| `turn_started` | `agentPhase = "waiting"`, `turnStartedMs = now`, `currentStep = 0`; **no longer calls `startAssistantMessage()`** |
| `llm_request` | `currentStep = step`; `agentPhase = "waiting"` unless currently `"approval"` |
| `reasoning_delta` | `m_conversation->appendReasoningDelta(delta)`; `agentPhase = "thinking"` |
| `text_delta` | `m_conversation->appendAssistantDelta(delta)`; `agentPhase = "responding"` |
| `tool_call` | `m_conversation->addToolCall(...)`; `agentPhase = "tool"`; `currentToolTitle = ConversationModel::toolTitle(name)` |
| `tool_result` | `m_conversation->upsertToolResult(...)`; `agentPhase = "waiting"` |
| `approval_required` | `enqueueApproval(data)`; `agentPhase = "approval"` |
| `approval_resolved` | `removeApprovalById(...)`; `agentPhase = "tool"` |
| `turn_completed` / `turn_error` / engine exit / `abortTurn()` | `m_conversation->finishReasoning()`; finish assistant message if applicable; `agentPhase = "idle"`, `turnRunning = false`, `currentToolTitle = ""` |

### `AppSettings` (`studio/src/core/AppSettings.{h,cpp}`)
Follow the existing `QSettings` pattern under `ui/` keys, with `Q_PROPERTY(... READ ... WRITE ... NOTIFY ...)` and `Q_INVOKABLE` setter for each:
- `QStringList enabledPlugins` (default `{"probe", "watch", "rtos", "console", "audit", "engine"}`) and `Q_INVOKABLE void setPluginEnabled(const QString &id, bool on)` + `setEnabledPlugins(const QStringList &list)`
- `QString activePlugin` (default `""`)
- `bool pluginPanelVisible` (default `false`)
- `int pluginPanelWidth` (default `400`, clamped `320..720`)
- `bool threadsPanelVisible` (default `true`)
- `bool showThinking` (default `true`)
- Include all 6 settings in `resetToDefaults()`.

---

## 1.3 QML Module File List & APIs

| Area | Files |
|---|---|
| `theme` | `Theme.qml`, `Icons.qml`, `PluginRegistry.qml` (singleton with `QT_QML_SINGLETON_TYPE TRUE` in CMake) |
| `components` (new) | `GradientButton.qml`, `IconChip.qml`, `Pill.qml`, `Chip.qml`, `AuroraBackdrop.qml`, `AgentOrb.qml`, `TypingDots.qml`, `PanelHeader.qml`, `SearchField.qml`, `ResizeHandle.qml` |
| `components` (restyle) | All existing components, preserving public properties & signals backward-compatibly |
| `shell` | `NavRail.qml`, `TopBar.qml`, `ThreadsPanel.qml`, `PluginBar.qml`, `PluginPanel.qml`, `StatusBar.qml`, `ScreenshotDriver.qml` |
| `pages` | `ChatPage.qml`, `SkillsPage.qml`, `PluginsPage.qml`, `HistoryPage.qml`, `SettingsPage.qml` |
| `chat` | `UserBubble.qml`, `ThinkingBlock.qml`, `ToolStep.qml`, `AnswerBlock.qml`, `NoticeRow.qml`, `ActivityPill.qml`, `Composer.qml`, `ChatEmptyState.qml`, `SkillPicker.qml` |
| `plugins` | `ProbePlugin.qml`, `WatchPlugin.qml`, `RtosPlugin.qml`, `ConsolePlugin.qml`, `AuditPlugin.qml`, `EngineLogPlugin.qml` |
| `dialogs` | Existing 7 (`CommandPalette.qml`, `ApprovalDialog.qml`, `OnboardingWizard.qml`, `LicenseDialog.qml`, `UpdateDialog.qml`, `CrashReportDialog.qml`, `AboutDialog.qml`) |

### Deleted after Phase C integration
`shell/ActivityBar.qml`, `shell/SideBar.qml`, `shell/BottomPanel.qml`, `pages/AgentPage.qml`, `pages/HardwarePage.qml`, `pages/WatchPage.qml`, `pages/RtosPage.qml`, `pages/AuditPage.qml`, `pages/SessionsPage.qml`.

### Page & Plugin APIs
- **PluginRegistry (`qml/theme/PluginRegistry.qml`):**
  - `plugins` property binding (re-evaluated on language change): array of `{id, name (qsTr), description (qsTr), icon, hue, category, tags}` in order `probe`, `watch`, `rtos`, `console`, `audit`, `engine`.
  - `comingSoon` property binding: `{id: "logic" | "hil" | "trace", ...}`.
  - Functions: `byId(id)`, `isEnabled(id)` (`Settings.enabledPlugins.indexOf(id) >= 0`), `skillMeta(cmd)` returning `{hue, icon}`, `riskHue(risk)`.
- **Plugin Component API:**
  - Root `Item`, `property bool compact: width < 560`, `signal toastRequested(string msg, string level)`. Must render cleanly from `320px` up to full width (two columns when `!compact`).
- **ChatPage:**
  - `signal openSettingsRequested(string section)`
  - `signal openApprovalRequested()`
  - `signal openPluginRequested(string pluginId)`
  - `function focusComposer()`
  - `function insertSkill(string cmd)`
  - `function sendCurrentPrompt()`
- **SkillsPage:**
  - `signal runSkillRequested(string cmd)`
- **PluginsPage:**
  - `signal openPluginRequested(string pluginId)`
- **HistoryPage:**
  - `signal sessionOpened()`
- **SettingsPage:**
  - Unchanged API (`property string activeSection`, `signal openLicenseRequested()`, `signal openUpdateRequested()`, `signal openAboutRequested()`, `signal openToast(string msg, string level)`).
- **Main.qml:**
  - `StackLayout` indices: `0` Chat, `1` Skills, `2` Plugins, `3` History, `4` Settings, `5` Plugin maximized.
  - Window properties: `activePage`, `threadsVisible`, `pluginPanelVisible`, `activePlugin`, `pluginMaximized`, `function openPlugin(id)` (sets `activePlugin`, shows panel, persists to `Settings`), existing dialog aliases (`approvalDialog`, `commandPalette`, `onboardingWizard`, `licenseDialog`, `updateDialog`, `crashReportDialog`, `aboutDialog`, `settingsPage`), plus `screenshotDriver` objectName on `ScreenshotDriver.qml` Loader.
  - Shortcuts: `Ctrl+1..4` pages, `Ctrl+,` settings, `Ctrl+B` threads, `Ctrl+J` plugin panel, `Ctrl+K`, `Ctrl+N`, `Ctrl+L`, `Ctrl+O`, `F5`, `Esc`.

---

## 1.4 Screenshot States (22 states × 2 themes × 2 languages = 88 PNGs)

Exact ordered list used identically in `Application.cpp`, `tests/tst_qml_smoke.cpp`, and `ScreenshotDriver.qml`:

| # | state | setup |
|---|---|---|
| 1 | `agent-empty` | page 0, threads on, panel off |
| 2 | `agent-conversation` | page 0, panel off |
| 3 | `agent-streaming` | page 0, `setAgentPhaseDemo("thinking")`, streaming thinking entry |
| 4 | `plugin-probe` | page 0, panel on, activePlugin `probe` |
| 5 | `plugin-watch` | page 0, panel on, activePlugin `watch` |
| 6 | `plugin-rtos` | page 0, panel on, activePlugin `rtos` |
| 7 | `plugin-console` | page 0, panel on, activePlugin `console` |
| 8 | `plugin-audit` | page 0, panel on, activePlugin `audit` |
| 9 | `plugin-maximized` | `watch` maximized, page 5 |
| 10 | `skills` | page 1 |
| 11 | `plugins` | page 2 |
| 12 | `history` | page 3 |
| 13 | `approval-danger` | Danger approval modal |
| 14 | `approval-diff` | Workspace diff approval modal |
| 15 | `settings-model` | page 4, section `model` |
| 16 | `settings-appearance` | page 4, section `appearance` |
| 17 | `license` | License dialog |
| 18 | `update-available` | Update available dialog |
| 19 | `onboarding-1` | Onboarding step 1 |
| 20 | `onboarding-3` | Onboarding step 3 |
| 21 | `palette` | Command palette (`Ctrl+K`) |
| 22 | `crash` | Crash recovery dialog |

*(22 states × 2 themes × 2 languages = 88 PNGs.)*

### `DemoSeeder` Contract
Seed telemetry, watch, rtos, logs, and audit data for **all** states (no longer gated per state).
- **`agent-conversation`**:
  1. user `"/crash Board resets with HardFault after ~30 s"`
  2. finished thinking, `4200` ms, ~400 chars of plausible reasoning
  3. `diagnose_crash` tool ok
  4. finished thinking, `1800` ms
  5. `inspect_board` tool ok
  6. final markdown answer with a heading, a register table, and numbered fix steps
  7. every non-streaming state calls `setAgentPhaseDemo("idle")`
- **`agent-streaming`**:
  - Same first 3 entries (`user`, finished thinking `4200` ms, `diagnose_crash` ok), then a streaming expanded thinking entry with partial text, plus `setAgentPhaseDemo("thinking")`.
