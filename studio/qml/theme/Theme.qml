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

    // Rendering capabilities (Software or offscreen renderer falls back to crisp 1px borderStrong)
    readonly property bool effectsEnabled: GraphicsInfo.api !== GraphicsInfo.Software && Qt.platform.pluginName !== "offscreen"

    // Surface & background tokens
    readonly property color bgCanvas: isDark ? "#0B0E14" : "#F6F8FB"
    readonly property color bgSidebar: isDark ? "#0F131A" : "#EEF1F6"
    readonly property color bgSurface: isDark ? "#141922" : "#FFFFFF"
    readonly property color bgPanel: isDark ? "#141922" : "#FFFFFF"
    readonly property color bgElevated: isDark ? "#1B222E" : "#F3F5F9"
    readonly property color bgHover: isDark ? "#232B38" : "#E6EAF2"
    readonly property color bgActive: isDark ? "#2A3444" : "#DCE2ED"
    readonly property color codeBg: isDark ? "#090C11" : "#F1F4F8"
    readonly property color scrim: isDark ? "#9905070B" : "#660F172A"

    // Border tokens
    readonly property color borderSubtle: isDark ? "#212836" : "#E4E8F0"
    readonly property color border: isDark ? "#2B3445" : "#D4DAE5"
    readonly property color borderStrong: isDark ? "#3C475C" : "#B4BECE"

    // Typography color tokens
    readonly property color textPrimary: isDark ? "#E6EDF5" : "#111827"
    readonly property color textSecondary: isDark ? "#A5B0C2" : "#4B5563"
    readonly property color textMuted: isDark ? "#6E7A8E" : "#6B7280"
    readonly property color textOnAccent: "#FFFFFF"

    // Brand & interactive accent tokens
    readonly property color accent: isDark ? "#4C7DFF" : "#2F63EB"
    readonly property color accentHover: isDark ? "#638EFF" : "#2554D4"
    readonly property color accentPressed: isDark ? "#3968EB" : "#1D44B8"
    readonly property color accentSubtle: isDark ? "#244C7DFF" : "#1A2F63EB"

    // Semantic status & hardware risk tokens
    readonly property color success: isDark ? "#34D399" : "#059669"
    readonly property color successSubtle: isDark ? "#1F34D399" : "#1A059669"
    readonly property color warning: isDark ? "#F59E0B" : "#D97706"
    readonly property color warningSubtle: isDark ? "#1FF59E0B" : "#1AD97706"
    readonly property color danger: isDark ? "#F43F5E" : "#E11D48"
    readonly property color dangerSubtle: isDark ? "#1FF43F5E" : "#1AE11D48"
    readonly property color info: isDark ? "#38BDF8" : "#0284C7"
    readonly property color infoSubtle: isDark ? "#1F38BDF8" : "#1A0284C7"
    readonly property color purple: isDark ? "#A78BFA" : "#7C3AED"
    readonly property color purpleSubtle: isDark ? "#1FA78BFA" : "#1A7C3AED"

    // Unified diff tokens
    readonly property color diffAddBg: isDark ? "#1A34D399" : "#18059669"
    readonly property color diffAddText: isDark ? "#6EE7B7" : "#065F46"
    readonly property color diffDelBg: isDark ? "#1AF43F5E" : "#18E11D48"
    readonly property color diffDelText: isDark ? "#FDA4AF" : "#9F1239"
    readonly property color diffHunkBg: isDark ? "#1A38BDF8" : "#180284C7"
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

    readonly property int weightNormal: Font.Normal
    readonly property int weightMedium: Font.Medium
    readonly property int weightSemiBold: Font.DemiBold
    readonly property int weightBold: Font.Bold

    // Motion timings (ms)
    readonly property int animFast: 120
    readonly property int animNormal: 180
    readonly property int animSlow: 260
    readonly property int easingType: Easing.OutCubic

    // Layout dimensions
    readonly property int activityBarWidth: 48
    readonly property int statusBarHeight: 26
    readonly property int focusRingWidth: 2
}
