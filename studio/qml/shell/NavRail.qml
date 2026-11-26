pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import McuStudio
import "../components"

Rectangle {
    id: root
    property int activePage: 0
    property int pendingApprovalsCount: 0

    signal pageSelected(int index)
    signal openLicenseRequested()

    implicitWidth: Theme.navRailWidth
    color: Theme.bgSidebar

    readonly property var navItems: [
        { page: 0, icon: Icons.messageSquare, label: qsTr("Chat"),    shortcut: "Ctrl+1" },
        { page: 1, icon: Icons.sparkles,      label: qsTr("Skills"),  shortcut: "Ctrl+2" },
        { page: 2, icon: Icons.puzzle,        label: qsTr("Plugins"), shortcut: "Ctrl+3" },
        { page: 3, icon: Icons.history,       label: qsTr("History"), shortcut: "Ctrl+4" }
    ]

    readonly property bool indicatorVisible: activePage >= 0 && activePage <= 4

    Divider {
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.right: parent.right
        orientation: Qt.Vertical
        width: 1
    }

    // Sliding 3px left-edge gradient indicator bar
    Rectangle {
        id: activeIndicator
        x: 0
        width: 3
        height: 22
        radius: 2
        visible: root.indicatorVisible
        y: {
            if (root.activePage >= 0 && root.activePage <= 3) {
                return navCol.y + root.activePage * (40 + Theme.s2) + 9
            }
            if (root.activePage === 4) {
                return bottomCol.y + settingsBtnWrap.y + 9
            }
            return 0
        }

        gradient: Gradient {
            orientation: Gradient.Vertical
            GradientStop { position: 0.0; color: Theme.brandA }
            GradientStop { position: 0.5; color: Theme.brandB }
            GradientStop { position: 1.0; color: Theme.brandC }
        }

        Behavior on y {
            NumberAnimation {
                duration: Theme.animationsEnabled ? 200 : 0
                easing.type: Theme.easingType
            }
        }
    }

    // Top section: Logo tile + Main nav items
    Column {
        id: topCol
        anchors.top: parent.top
        anchors.topMargin: Theme.s3
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Theme.s3

        // 36px rounded gradient logo tile (brandA -> brandC, Icons.cpu white)
        Rectangle {
            id: logoTile
            anchors.horizontalCenter: parent.horizontalCenter
            width: 36
            height: 36
            radius: Theme.radiusLg

            gradient: Gradient {
                orientation: Gradient.Vertical
                GradientStop { position: 0.0; color: Theme.brandA }
                GradientStop { position: 0.5; color: Theme.brandB }
                GradientStop { position: 1.0; color: Theme.brandC }
            }

            Icon {
                anchors.centerIn: parent
                name: Icons.cpu
                size: 20
                color: "#FFFFFF"
            }

            HoverHandler { id: logoHover }
            TapHandler { onTapped: root.pageSelected(0) }

            AppToolTip {
                visible: logoHover.hovered
                text: qsTr("MCU Harness Studio")
            }
        }

        Divider {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: Theme.s3
            anchors.rightMargin: Theme.s3
        }
    }

    Column {
        id: navCol
        anchors.top: topCol.bottom
        anchors.topMargin: Theme.s3
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Theme.s2

        Repeater {
            model: root.navItems

            delegate: Item {
                id: navItem
                required property var modelData
                readonly property bool isActive: root.activePage === modelData.page

                width: root.width
                height: 40

                Rectangle {
                    anchors.centerIn: parent
                    width: 40
                    height: 40
                    radius: Theme.radiusLg
                    color: navItem.isActive
                        ? Theme.alpha(Theme.accent, 0.14)
                        : (navHover.hovered ? Theme.bgHover : "transparent")

                    Behavior on color {
                        ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                    }

                    Icon {
                        anchors.centerIn: parent
                        name: navItem.modelData.icon
                        size: 19
                        color: navItem.isActive
                            ? Theme.accent
                            : (navHover.hovered ? Theme.textPrimary : Theme.textMuted)
                    }

                    // Pending approval dot on Chat
                    Rectangle {
                        visible: navItem.modelData.page === 0 && root.pendingApprovalsCount > 0
                        anchors.top: parent.top
                        anchors.right: parent.right
                        anchors.topMargin: 5
                        anchors.rightMargin: 5
                        width: 7
                        height: 7
                        radius: 4
                        color: Theme.hueAmber
                    }

                    HoverHandler {
                        id: navHover
                        cursorShape: Qt.PointingHandCursor
                    }
                    TapHandler {
                        onTapped: root.pageSelected(navItem.modelData.page)
                    }

                    AppToolTip {
                        visible: navHover.hovered
                        text: navItem.modelData.label
                        shortcut: navItem.modelData.shortcut
                    }
                }
            }
        }
    }

    // Bottom section: Theme toggle, Settings, License status dot
    Column {
        id: bottomCol
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Theme.s3
        anchors.left: parent.left
        anchors.right: parent.right
        spacing: Theme.s2

        // Theme toggle
        Item {
            width: root.width
            height: 36

            Rectangle {
                anchors.centerIn: parent
                width: 36
                height: 36
                radius: Theme.radiusLg
                color: themeHover.hovered ? Theme.bgHover : "transparent"

                Behavior on color {
                    ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                }

                Icon {
                    anchors.centerIn: parent
                    name: Theme.isDark ? Icons.sun : Icons.moon
                    size: 18
                    color: themeHover.hovered ? Theme.textPrimary : Theme.textMuted
                }

                HoverHandler {
                    id: themeHover
                    cursorShape: Qt.PointingHandCursor
                }
                TapHandler {
                    onTapped: Settings.setTheme(Theme.isDark ? "light" : "dark")
                }

                AppToolTip {
                    visible: themeHover.hovered
                    text: Theme.isDark ? qsTr("Switch to Light Theme") : qsTr("Switch to Dark Theme")
                }
            }
        }

        // Settings
        Item {
            id: settingsBtnWrap
            width: root.width
            height: 40
            readonly property bool isActive: root.activePage === 4

            Rectangle {
                anchors.centerIn: parent
                width: 40
                height: 40
                radius: Theme.radiusLg
                color: settingsBtnWrap.isActive
                    ? Theme.alpha(Theme.accent, 0.14)
                    : (settingsHover.hovered ? Theme.bgHover : "transparent")

                Behavior on color {
                    ColorAnimation { duration: Theme.animationsEnabled ? Theme.animFast : 0 }
                }

                Icon {
                    anchors.centerIn: parent
                    name: Icons.settings
                    size: 19
                    color: settingsBtnWrap.isActive
                        ? Theme.accent
                        : (settingsHover.hovered ? Theme.textPrimary : Theme.textMuted)
                }

                HoverHandler {
                    id: settingsHover
                    cursorShape: Qt.PointingHandCursor
                }
                TapHandler {
                    onTapped: root.pageSelected(4)
                }

                AppToolTip {
                    visible: settingsHover.hovered
                    text: qsTr("Settings")
                    shortcut: "Ctrl+,"
                }
            }
        }

        // Compact license status dot button
        Item {
            id: licWrap
            width: root.width
            height: 30

            readonly property color licDotColor: {
                if (License.status === "active") return Theme.hueEmerald
                if (License.status === "expired") return Theme.hueRose
                return Theme.hueAmber
            }

            Rectangle {
                anchors.centerIn: parent
                width: 34
                height: 26
                radius: Theme.radiusPill
                color: licHover.hovered ? Theme.bgHover : Theme.bgElevated
                border.color: licHover.hovered ? Theme.borderStrong : Theme.borderSubtle
                border.width: 1

                Row {
                    anchors.centerIn: parent
                    spacing: 4

                    StatusDot {
                        anchors.verticalCenter: parent.verticalCenter
                        size: 6
                        color: licWrap.licDotColor
                    }

                    Icon {
                        anchors.verticalCenter: parent.verticalCenter
                        name: Icons.keyRound
                        size: 11
                        color: licHover.hovered ? Theme.textPrimary : Theme.textMuted
                    }
                }

                HoverHandler {
                    id: licHover
                    cursorShape: Qt.PointingHandCursor
                }
                TapHandler {
                    onTapped: root.openLicenseRequested()
                }

                AppToolTip {
                    visible: licHover.hovered
                    text: {
                        if (License.status === "active") return qsTr("License: %1").arg(License.tier || qsTr("Pro"))
                        if (License.status === "expired") return qsTr("License Expired — Click to Activate")
                        return qsTr("Trial · %1 days left").arg(License.trialDaysRemaining || 0)
                    }
                }
            }
        }
    }
}
