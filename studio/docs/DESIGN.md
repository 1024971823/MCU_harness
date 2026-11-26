# MCU Harness Studio v2 "Aurora" — Visual Design Specification

## 0.1 Product shape (modelled on Claude Desktop / Cursor Agent / Antigravity)
Chat sits in the middle. Plugins live in a right-hand plugin bar and open as a side panel. Skills, Plugins, History and Settings are separate pages reached from the left nav rail. Nothing is stacked in one sidebar any more.

```text
┌────┬──────────────────────────────────────────────────────────────────┬────┐
│Nav │ TopBar 48px: [project pill ▾]   thread title   [engine●][⌘K][◐] │Plug│
│Rail├──────────────┬───────────────────────────────┬───────────────────┤ in │
│60px│ ThreadsPanel │  ChatPage (content max 800px, │ PluginPanel       │Bar │
│logo│ 272px        │  centered)                    │ 400px (320–720,   │48px│
│Chat│ +New chat    │  user bubble / thinking /     │ drag-resizable,   │ ⚡ │
│Skil│ search       │  tool steps / answer          │ maximize ⤢ → full │ 📈 │
│Plug│ Today/Yday/… │  [ActivityPill: Thinking 3.1s]│ page)             │ 🧵 │
│Hist│ target mini  │  [Composer ───────────── ➤]   │                   │ >_ │
│ ⚙  │ card         │                               │                   │ 🛡 │
├────┴──────────────┴───────────────────────────────┴───────────────────┴────┤
│ StatusBar 24px (colored segments)                                          │
└────────────────────────────────────────────────────────────────────────────┘
```
- The ThreadsPanel shows only on the Chat page and toggles with `Ctrl+B`. The PluginPanel toggles with `Ctrl+J`.
- The BottomPanel is removed. RTT/UART moves into the **Serial Console** plugin.

## 0.2 Colour tokens (`Theme.qml`, dark / light)
Keep **every existing token name** with new values, and add the new ones.

| token | dark | light |
|---|---|---|
| bgCanvas | `#09090F` | `#F7F7FB` |
| bgSidebar (rail + threads) | `#0C0C14` | `#F0F0F7` |
| bgSurface / bgPanel | `#111119` / `#13131C` | `#FFFFFF` / `#FFFFFF` |
| bgElevated / bgHover / bgActive | `#1A1A25` / `#20202D` / `#2A2A3A` | `#F4F4FA` / `#ECECF5` / `#E2E2EF` |
| codeBg | `#0B0B12` | `#F3F3F9` |
| borderSubtle / border / borderStrong | `#1E1E2A` / `#2A2A3A` / `#3A3A4E` | `#E8E8F0` / `#DCDCE8` / `#C4C4D4` |
| textPrimary / Secondary / Muted | `#ECECF4` / `#A6A6BC` / `#6E6E86` | `#14141F` / `#4A4A5E` / `#77778C` |
| accent / Hover / Pressed | `#7C6CFF` / `#8F82FF` / `#6A5AF0` | `#6D5DF6` / `#5B4AE8` / `#4C3CD6` |
| brandA / brandB / brandC (gradient) | `#8B5CF6` / `#6366F1` / `#22D3EE` | `#7C3AED` / `#4F46E5` / `#0891B2` |
| hueViolet / hueIndigo / hueCyan / hueSky | `#A78BFA` / `#818CF8` / `#22D3EE` / `#38BDF8` | `#7C3AED` / `#4F46E5` / `#0891B2` / `#0284C7` |
| hueEmerald / hueAmber / hueOrange / hueRose | `#34D399` / `#FBBF24` / `#FB923C` / `#FB7185` | `#059669` / `#D97706` / `#EA580C` / `#E11D48` |
| huePink / hueLime / hueSlate | `#F472B6` / `#A3E635` / `#94A3B8` | `#DB2777` / `#65A30D` / `#475569` |

- Point the existing semantic tokens at hues: `success=hueEmerald`, `warning=hueAmber`, `danger=hueRose`, `info=hueSky`, `purple=hueViolet`. `*Subtle` = the hue at alpha 0.12 (dark) or 0.10 (light).
- Add `function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }`.
- Add `readonly property bool animationsEnabled: !AppInfo.screenshotMode`. All `Behavior` and transition durations become `Theme.animationsEnabled ? N : 0`. Infinite loops such as the spinner, dots and shimmer may keep running.
- New sizes: `navRailWidth` 60, `topBarHeight` 48, `statusBarHeight` 24, `pluginBarWidth` 48, `threadsPanelWidth` 272, `pluginPanelDefaultWidth` 400, `chatMaxWidth` 800, `composerRadius` 18, `radius2xl` 20. Typography stays Inter / JetBrains Mono. Add `chatTextSize` 14 and `chatLineHeight` 1.6.
- Motion: `animFast` 120, `animNormal` 200, `animSlow` 320, `OutCubic`.

## 0.3 Plugin and skill identity colours
- **Plugins:** `probe`=hueAmber (`cpu`), `watch`=hueCyan (`variable`), `rtos`=hueEmerald (`layers`), `console`=hueSky (`square-terminal`), `audit`=hueRose (`shield-check`), `engine`=hueSlate (`scroll-text`).
- **Coming soon:** `logic`=huePink (`activity`), `hil`=hueLime (`flask-conical`), `trace`=hueOrange (`timer`).
- **Skills:** `/crash`=hueRose (`bug`), `/doctor`=hueAmber (`waypoints`), `/bringup`=hueEmerald (`circuit-board`), `/refactor`=hueViolet (`wand-sparkles`), `/hil`=hueCyan (`flask-conical`). Anything else gets hueIndigo (`sparkles`).
- **Tool steps by risk:** `read_only`=hueCyan, `workspace_write`=hueViolet, `shell`=hueSky, `hardware_state`=hueAmber, `hardware_danger`=hueRose.

## 0.4 Key components
- **NavRail:**
  - The top has a 36px gradient logo tile (brandA→brandC, `cpu` icon, white).
  - Items: Chat (`message-square`), Skills (`sparkles`), Plugins (`puzzle`), History (`history`). The bottom has a theme toggle (`sun`/`moon`), Settings (`settings`) and a license badge dot.
  - The active item gets an accent-tinted 40px rounded square, plus a 3px gradient indicator on the left edge that slides between items (y Behavior 200ms). Every item has a tooltip.
- **TopBar:**
  - Left: project pill (folder icon + project basename + device chip in hueAmber; click opens the folder dialog).
  - Centre: thread title, textSecondary 13px semibold.
  - Right: engine status chip (dot: emerald ready, amber starting, rose error), ⌘K search button showing Kbd "Ctrl K", plugin-panel toggle (`panel-right-open`/`panel-right-close`).
- **ThreadsPanel:**
  - Full-width GradientButton "New chat" (`square-pen`, `Ctrl+N`), then a SearchField.
  - Threads grouped "Today / Yesterday / Earlier" from SessionModel. Each row shows title, one-line preview and time. The current thread has bgActive and a 2px accent bar. Hover shows bgHover.
  - The bottom has a mini target card (device, probe dot, "Open Probe ›") that opens the probe plugin.
- **ChatPage:**
  - **Empty state:** AuroraBackdrop behind it (3 big soft circles violet/indigo/cyan at opacity .10–.18, slowly drifting over 14–18s; blur via MultiEffect only when `Theme.effectsEnabled`). Then a 64px AgentOrb, the greeting "What are we debugging today?", a subtitle, and a 2×2 grid of colourful skill cards (icon chip in the skill hue, mono command badge, title, one-line description; hover lifts with border = hue).
  - **User message:** right-aligned, max 72% width, bg `alpha(accent,.12)`, border `alpha(accent,.35)`, radius 16 with bottomRightRadius 4. Hover shows a copy button.
  - **Assistant turn:** at `groupStart` show a 28px AgentOrb, "Harness Agent" and a model chip (`Workbench.model`). Blocks are indented 40px. A 1px `borderSubtle` timeline line runs down the left of the turn's blocks from groupStart to groupEnd.
  - **ThinkingBlock** (the Antigravity-style chain of thought): a 2px vertical gradient rule (brandA→brandC) on the left.
    - Header while streaming: `brain` icon in hueViolet, the label "Thinking", TypingDots, and a live elapsed timer "3.4s" (100ms Timer from `startedMs`).
    - Header when finished: "Thought for 4.2s" plus a chevron.
    - Body when expanded: the full reasoning in textSecondary 13px, line-height 1.55. Collapsed while streaming: the last ~220 characters in muted italic.
    - It defaults to expanded while streaming and auto-collapses when finished unless the user toggled it. Clicking the header toggles (`model.setExpanded`). When `Settings.showThinking` is false, render only the one-line header and do not allow expanding.
  - **ToolStep:**
    - 28px IconChip tinted by risk hue, tool title (semibold 13), compact args summary (first 1–2 key=value pairs, mono 12, muted, elided).
    - Right side by status: running = Spinner + live elapsed; ok = emerald `circle-check` + duration; error = rose `circle-x`; denied = rose `shield-off` + "Blocked". A risk Badge shows for anything except `read_only`.
    - Click expands to show Arguments / Result via CodeBlock/JsonView, with a copy button.
    - The last running step shows an amber inline "Awaiting approval · Review" button (opens the approval dialog) while `Workbench.agentPhase === "approval"`.
  - **AnswerBlock:** MarkdownText at 14px / 1.6. While streaming, append a blinking "▍" caret (Timer, 530ms). On hover, show a row with copy and retry (retry only on the last answer), plus the timestamp.
  - **NoticeRow:** a coloured pill by level.
  - **"Pending" row:** while `agentPhase === "waiting"` and the last entry is the user's message, show an AgentOrb plus a shimmering "Connecting to model…" row.
  - **Auto-scroll:** stick to the bottom while the user is at the bottom. Otherwise show a floating round "↓ Jump to latest" button.
  - **List add transition:** opacity 0→1, y +8→0, 200ms.
- **ActivityPill** (above the composer, only while `turnRunning`): pill with a gradient border, a rotating AgentOrb, the phase text and elapsed seconds, "Step N", and a Stop button (Esc).
  - waiting: "Connecting to model…"
  - thinking: "Thinking…"
  - responding: "Writing response…"
  - tool: "Running %1…" (`currentToolTitle`)
  - approval: amber "Waiting for your approval" with a Review button
- **Composer:** floating card, radius 18, bgSurface, 1px border that becomes a brand gradient border on focus (outer gradient Rectangle with an inner Rectangle 1px inset). Multiline input with Enter to send and Shift+Enter for a newline. Bottom toolbar:
  - Skills button (`sparkles` "/") that opens SkillPicker.
  - Model pill (provider · model; click goes to Settings → model).
  - Thinking toggle (`brain`, bound to `Settings.showThinking`).
  - Interlock pill: `read_only` emerald, `interactive` amber, `auto_safe` rose; click cycles.
  - Round 36px gradient send button (`arrow-up`) that crossfades into a rose Stop (`square`) while running.
  - Typing "/" at the start opens SkillPicker: a coloured list, keyboard ↑↓ Enter.
- **PluginBar:**
  - Shows the enabled plugins only. Each button is 36px. Active: rounded square tinted `alpha(hue,.16)`, the icon in the hue, and a 2px hue bar on the right edge. Inactive: textMuted, hover textPrimary. Tooltips.
  - Badges: probe status dot (emerald when connected), rtos hazards count (rose), audit denied count (rose).
  - The bottom "+" button opens the Plugins page. Clicking the active plugin closes the panel.
- **PluginPanel:**
  - PanelHeader: IconChip in the hue, plugin name, a maximize ⤢ / restore button (`maximize-2`/`minimize-2`), and close ✕.
  - Body is a Loader switch on `activePlugin` using `sourceComponent` Components declared inside PluginPanel.
  - The left ResizeHandle (4px, accent on hover) drags the width, clamped to 320..720 and persisted. The width animates 0↔w over 220ms on open and close.
  - Maximize shows the same plugin full-width as StackLayout page 5.
- **Plugins page:**
  - Gradient banner header with title, subtitle, SearchField, and category Chips (All / Hardware / Debug / Safety / Observability).
  - Card grid, minimum card width 280. Each card: an 8px top strip in the hue gradient, a 40px IconChip, name, "Built-in" badge, version "1.0.0", description, tag chips, and a footer with an AppSwitch (enabled) and an "Open" button.
  - A "Coming soon" section holds 3 dimmed cards with a "Soon" badge.
- **Skills page:**
  - Banner, then a grid of skill cards from PlaybookModel: hue IconChip, mono command badge, title, description, and a "Run in chat" GradientButton that inserts the command into the composer and switches to Chat.
  - A "Quick commands" section holds Chips for `/status` `/audit` `/compact` `/clear` `/sessions` `/help` that insert into the composer.
- **History page:** the SessionsPage restyled as a searchable list of rich rows.
- **Settings page:** keep its sections. Restyle the section nav to rail-style items. In Appearance, add "Show thinking chain" (`Settings.showThinking`) and "Enabled plugins" switches.
- **Dialogs** (via AppDialog): radius 20, bgSurface, a 2px brand-gradient hairline at the top, scrim. ApprovalDialog gets a danger header strip (rose→orange) with a shield icon. CommandPalette is 640px wide, with grouped results and coloured icon chips. Add groups for plugins ("Open plugin: …"), skills and pages.
- **StatusBar** (24px): engine dot and label, provider/model, probe state (amber chip), interlock mode (coloured), tokens, pending approvals (amber pill when > 0), license, and version.
- **Software renderer rules:** no shaders unless `Theme.effectsEnabled`. Gradients use `Rectangle.gradient` (OK in software). Per-corner radii are allowed (Qt 6.11).
