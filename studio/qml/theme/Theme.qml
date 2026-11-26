pragma Singleton
import QtQuick
import McuStudio

Item {
    id: root
    visible: false
    width: 0
    height: 0

    // Theme resolution ("system" | "dark" | "light")
    readonly property string mode: Settings.theme ? Settings.theme : "system"
    readonly property bool systemIsDark: Application.styleHints.colorScheme === Qt.Dark
    readonly property bool isDark: mode === "dark" || (mode === "system" && systemIsDark)

    // Rendering capabilities (Software or offscreen renderer falls back to crisp borders without shader layers)
    readonly property bool effectsEnabled: GraphicsInfo.api !== GraphicsInfo.Software && Qt.platform.pluginName !== "offscreen"
    readonly property bool animationsEnabled: !AppInfo.screenshotMode

    function alpha(c, a) {
        return Qt.rgba(c.r, c.g, c.b, a)
    }

    // Surface & background tokens (Aurora)
    readonly property color bgCanvas: isDark ? "#09090F" : "#F7F7FB"
    readonly property color bgSidebar: isDark ? "#0C0C14" : "#F0F0F7"
    readonly property color bgSurface: isDark ? "#111119" : "#FFFFFF"
    readonly property color bgPanel: isDark ? "#13131C" : "#FFFFFF"
    readonly property color bgElevated: isDark ? "#1A1A25" : "#F4F4FA"
    readonly property color bgHover: isDark ? "#20202D" : "#ECECF5"
    readonly property color bgActive: isDark ? "#2A2A3A" : "#E2E2EF"
    readonly property color codeBg: isDark ? "#0B0B12" : "#F3F3F9"
    readonly property color scrim: isDark ? "#9E04040A" : "#5C0A0A14"

    // Border tokens
    readonly property color borderSubtle: isDark ? "#1E1E2A" : "#E8E8F0"
    readonly property color border: isDark ? "#2A2A3A" : "#DCDCE8"
    readonly property color borderStrong: isDark ? "#3A3A4E" : "#C4C4D4"

    // Typography color tokens
    readonly property color textPrimary: isDark ? "#ECECF4" : "#14141F"
    readonly property color textSecondary: isDark ? "#A6A6BC" : "#4A4A5E"
    readonly property color textMuted: isDark ? "#6E6E86" : "#77778C"
    readonly property color textOnAccent: "#FFFFFF"

    // Brand & interactive accent tokens
    readonly property color accent: isDark ? "#7C6CFF" : "#6D5DF6"
    readonly property color accentHover: isDark ? "#8F82FF" : "#5B4AE8"
    readonly property color accentPressed: isDark ? "#6A5AF0" : "#4C3CD6"
    readonly property color accentSubtle: alpha(accent, isDark ? 0.14 : 0.10)

    readonly property color brandA: isDark ? "#8B5CF6" : "#7C3AED"
    readonly property color brandB: isDark ? "#6366F1" : "#4F46E5"
    readonly property color brandC: isDark ? "#22D3EE" : "#0891B2"

    // Identity & category hue tokens
    readonly property color hueViolet: isDark ? "#A78BFA" : "#7C3AED"
    readonly property color hueIndigo: isDark ? "#818CF8" : "#4F46E5"
    readonly property color hueCyan: isDark ? "#22D3EE" : "#0891B2"
    readonly property color hueSky: isDark ? "#38BDF8" : "#0284C7"
    readonly property color hueEmerald: isDark ? "#34D399" : "#059669"
    readonly property color hueAmber: isDark ? "#FBBF24" : "#D97706"
    readonly property color hueOrange: isDark ? "#FB923C" : "#EA580C"
    readonly property color hueRose: isDark ? "#FB7185" : "#E11D48"
    readonly property color huePink: isDark ? "#F472B6" : "#DB2777"
    readonly property color hueLime: isDark ? "#A3E635" : "#65A30D"
    readonly property color hueSlate: isDark ? "#94A3B8" : "#475569"

    // Semantic status & hardware risk tokens
    readonly property color success: hueEmerald
    readonly property color successSubtle: alpha(hueEmerald, isDark ? 0.12 : 0.10)
    readonly property color warning: hueAmber
    readonly property color warningSubtle: alpha(hueAmber, isDark ? 0.12 : 0.10)
    readonly property color danger: hueRose
    readonly property color dangerSubtle: alpha(hueRose, isDark ? 0.12 : 0.10)
    readonly property color info: hueSky
    readonly property color infoSubtle: alpha(hueSky, isDark ? 0.12 : 0.10)
    readonly property color purple: hueViolet
    readonly property color purpleSubtle: alpha(hueViolet, isDark ? 0.12 : 0.10)

    // Unified diff tokens
    readonly property color diffAddBg: alpha(hueEmerald, isDark ? 0.11 : 0.09)
    readonly property color diffAddText: isDark ? "#6EE7B7" : "#065F46"
    readonly property color diffDelBg: alpha(hueRose, isDark ? 0.11 : 0.09)
    readonly property color diffDelText: isDark ? "#FDA4AF" : "#9F1239"
    readonly property color diffHunkBg: alpha(hueSky, isDark ? 0.11 : 0.09)
    readonly property color diffHunkText: isDark ? "#7DD3FC" : "#075985"

    // 4px spacing grid
    readonly property int s1: 4
    readonly property int s2: 8
    readonly property int s3: 12
    readonly property int s4: 16
    readonly property int s5: 20
    readonly property int s6: 24
    readonly property int s7: 28
    readonly property int s8: 32

    // Radii
    readonly property int sm: 4
    readonly property int md: 6
    readonly property int lg: 10
    readonly property int xl: 14
    readonly property int radiusSm: 4
    readonly property int radiusMd: 6
    readonly property int radiusLg: 10
    readonly property int radiusXl: 14
    readonly property int composerRadius: 18
    readonly property int radius2xl: 20
    readonly property int radiusPill: 999

    // Typography
    readonly property string fontSans: "Inter"
    readonly property string fontMono: "JetBrains Mono"

    readonly property int textXs: 11
    readonly property int textSm: 12
    readonly property int textBase: 13
    readonly property int textMd: 14
    readonly property int textLg: 16
    readonly property int textXl: 20
    readonly property int text2xl: 28

    readonly property int chatTextSize: 14
    readonly property real chatLineHeight: 1.6

    readonly property int weightNormal: Font.Normal
    readonly property int weightMedium: Font.Medium
    readonly property int weightSemiBold: Font.DemiBold
    readonly property int weightBold: Font.Bold

    // Motion timings (ms)
    readonly property int animFast: 120
    readonly property int animNormal: 200
    readonly property int animSlow: 320
    readonly property int easingType: Easing.OutCubic

    // Layout dimensions
    readonly property int activityBarWidth: 48
    readonly property int navRailWidth: 60
    readonly property int topBarHeight: 48
    readonly property int statusBarHeight: 24
    readonly property int pluginBarWidth: 48
    readonly property int threadsPanelWidth: 272
    readonly property int pluginPanelDefaultWidth: 400
    readonly property int chatMaxWidth: 800
    readonly property int focusRingWidth: 2
}
