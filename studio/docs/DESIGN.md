# MCU Harness Studio — Design System (`studio/docs/DESIGN.md`)

A commercial-grade Qt 6 QML desktop IDE design language inspired by Cursor, JetBrains Fleet, and VS Code, tailored for embedded Cortex-M firmware debugging and Hardware-in-the-Loop (HITL) safety engineering.

---

## 1. Color Tokens (`Theme.qml`)

All colors adapt dynamically between **Dark** and **Light** palettes via `Settings.theme` (`"dark"`, `"light"`, or `"system"` bound to `Application.styleHints.colorScheme`).

| Token | Dark | Light | Usage |
| :--- | :--- | :--- | :--- |
| `bgCanvas` | `#0B0E14` | `#F6F8FB` | Main viewport & editor canvas |
| `bgSidebar` | `#0F131A` | `#EEF1F6` | Activity bar, sidebar, bottom panel, status bar |
| `bgSurface` | `#141922` | `#FFFFFF` | Cards, dialogs, popups, table headers |
| `bgElevated` | `#1B222E` | `#F3F5F9` | Inputs, buttons, interactive chips |
| `bgHover` | `#232B38` | `#E6EAF2` | Hover surface state |
| `bgActive` | `#2A3444` | `#DCE2ED` | Pressed / selected item fill |
| `codeBg` | `#090C11` | `#F1F4F8` | Monospace code, diff, JSON & log containers |
| `borderSubtle` | `#212836` | `#E4E8F0` | Hairline dividers & subtle card outlines |
| `border` | `#2B3445` | `#D4DAE5` | Standard control borders |
| `borderStrong` | `#3C475C` | `#B4BECE` | Dialog outer border & high-contrast fallback |
| `textPrimary` | `#E6EDF5` | `#111827` | Primary body text & headings |
| `textSecondary` | `#A5B0C2` | `#4B5563` | Supporting descriptions & labels |
| `textMuted` | `#6E7A8E` | `#6B7280` | Placeholders, timestamps, tertiary metadata |
| `accent` | `#4C7DFF` | `#2F63EB` | Primary actions, active nav indicator, focus ring |
| `accentHover` | `#638EFF` | `#2554D4` | Hovered primary CTA |
| `accentPressed` | `#3968EB` | `#1D44B8` | Pressed primary CTA |
| `accentSubtle` | `#1E4C7DFF` | `#1A2F63EB` | Selected chips, live-watch change flash |
| `success` | `#34D399` | `#059669` | Probe connected, allow, stack healthy (`<70%`) |
| `warning` | `#F59E0B` | `#D97706` | Hardware state risk, stack caution (`70–85%`), errors |
| `danger` | `#F43F5E` | `#E11D48` | `hardware_danger` HITL strip, bricking block, stack `>85%` |
| `info` | `#38BDF8` | `#0284C7` | SVD/diff hunk headers, informational callouts |
| `purple` | `#A78BFA` | `#7C3AED` | Shell risk badge, AI playbook accent |

---

## 2. Type Scale

- **UI Sans**: `Inter` (`Regular` 400, `Medium` 500, `SemiBold` 600)
- **Monospace**: `JetBrains Mono` (`Regular` 400, `Medium` 500, `Bold` 700)

| Token | Size (px) | Weight | Usage |
| :--- | :--- | :--- | :--- |
| `textXs` | `11` | 500 | Badges, status bar items, keycaps (`Kbd`), table headers |
| `textSm` | `12` | 400 / 500 | Secondary labels, tool summaries, logs, code blocks |
| `textBase` | `13` | 400 / 500 | Standard UI controls, chat body, sidebar items |
| `textMd` | `14` | 500 / 600 | Card titles, dialog headers, primary input |
| `textLg` | `16` | 600 | Section & page headers |
| `textXl` | `20` | 600 | Onboarding step titles, page hero headers |
| `text2xl` | `28` | 600 | Welcome headline, empty-state display title |

---

## 3. Spacing Grid & Radii

All layout spacing uses a strict **4 px** grid (`s1`..`s8`):

- `s1 = 4`, `s2 = 8`, `s3 = 12`, `s4 = 16`, `s5 = 20`, `s6 = 24`, `s7 = 28`, `s8 = 32`

Corner radii:

- `sm = 4`: Keycaps (`Kbd`), small badges, inline code tags
- `md = 6`: Inputs, buttons, combo boxes, list rows
- `lg = 10`: Cards, banners, tool call containers, toasts
- `xl = 14`: Modal dialogs, command palette, onboarding wizard

---

## 4. Elevation, Effects & Motion

- **Hardware-Accelerated Effects**: `Theme.effectsEnabled` evaluates `GraphicsInfo.api !== GraphicsInfo.Software`. Modal dialogs and floating popovers render `MultiEffect` drop shadows when `Theme.effectsEnabled` is true and fall back cleanly to `borderStrong` 1 px framing under software rendering (`llvmpipe`, headless offscreen screenshot capture).
- **Motion Timings** (`Easing.OutCubic`):
  - `animFast = 120 ms`: hover/press transitions, focus ring fade, toggle thumb slide
  - `animNormal = 180 ms`: dialog/toast entrance, collapsible panels, tab underline
  - `animSlow = 260 ms`: sidebar/bottom panel transitions, `600 ms` `accentSubtle` Live Watch value-change highlight

---

## 5. Keyboard & Accessibility Rules

1. **Focus Rings**: Every interactive control renders a crisp `2 px` `Theme.accent` focus ring exclusively when `visualFocus` is true (keyboard Tab navigation, never noisy on mouse click).
2. **Global Application Shortcuts** (`Qt.ApplicationShortcut`):
   - `Ctrl+K` / `Ctrl+Shift+P`: Command Palette
   - `Ctrl+B`: Toggle Left Sidebar
   - `Ctrl+J`: Toggle Bottom Console (RTT / UART / Engine)
   - `Ctrl+,`: Open Settings
   - `Ctrl+1`..`Ctrl+6`: Switch primary views (Agent, Hardware, Watch, RTOS, Sessions, Audit)
   - `Ctrl+L`: Focus Agent Prompt Composer
   - `Ctrl+N`: New Agent Session
   - `Ctrl+O`: Open Project Folder
   - `F5`: Refresh Hardware Telemetry Snapshot
   - `Esc`: Close active palette, non-critical dialog, or abort running turn
3. **Hit Targets & Contrast**: Primary buttons and list rows enforce `>= 32 px` height (`40 px` for ActivityBar rail icons), `AppScrollBar` is `8 px` wide and fades smoothly on hover/active, and critical HITL `ApprovalDialog` gates require explicit user decision (`Esc` does not silently approve or dismiss critical hardware danger prompts).
