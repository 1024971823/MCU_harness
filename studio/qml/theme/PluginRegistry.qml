pragma Singleton
import QtQuick
import McuStudio

QtObject {
    id: root

    // Binding expression so engine.retranslate() dynamically updates translated names/descriptions/tags.
    readonly property var plugins: [
        {
            id: "probe",
            name: qsTr("Probe & Target"),
            description: qsTr("SWD/JTAG debug probe control, core halt/resume, register & fault inspection"),
            icon: "cpu",
            hue: Theme.hueAmber,
            category: "hardware",
            tags: [qsTr("SWD"), qsTr("Registers"), qsTr("Fault")]
        },
        {
            id: "watch",
            name: qsTr("Live Watch"),
            description: qsTr("DWARF symbol live watch, real-time variable sampling, and memory expressions"),
            icon: "variable",
            hue: Theme.hueCyan,
            category: "observability",
            tags: [qsTr("DWARF"), qsTr("Telemetry"), qsTr("Memory")]
        },
        {
            id: "rtos",
            name: qsTr("RTOS Inspector"),
            description: qsTr("Task states, stack high-water marks, CPU utilization, and hazard detection"),
            icon: "layers",
            hue: Theme.hueEmerald,
            category: "debug",
            tags: [qsTr("FreeRTOS"), qsTr("Stacks"), qsTr("Hazards")]
        },
        {
            id: "console",
            name: qsTr("Serial Console"),
            description: qsTr("RTT & UART live log stream, severity filtering, and raw diagnostics"),
            icon: "square-terminal",
            hue: Theme.hueSky,
            category: "observability",
            tags: [qsTr("UART"), qsTr("RTT"), qsTr("Logs")]
        },
        {
            id: "audit",
            name: qsTr("Safety Audit"),
            description: qsTr("HITL interlock policy log, approved/denied tool calls, and risk trail"),
            icon: "shield-check",
            hue: Theme.hueRose,
            category: "safety",
            tags: [qsTr("HITL"), qsTr("Interlock"), qsTr("Audit")]
        },
        {
            id: "engine",
            name: qsTr("Engine Logs"),
            description: qsTr("Agent runtime RPC diagnostics, model requests, and execution trace"),
            icon: "scroll-text",
            hue: Theme.hueSlate,
            category: "observability",
            tags: [qsTr("RPC"), qsTr("Runtime"), qsTr("Trace")]
        }
    ]

    readonly property var comingSoon: [
        {
            id: "logic",
            name: qsTr("Logic Analyzer"),
            description: qsTr("Protocol decoder for SPI, I2C, CAN, and SWO digital bus captures"),
            icon: "activity",
            hue: Theme.huePink,
            category: "hardware",
            tags: [qsTr("SPI/I2C"), qsTr("Decoder")]
        },
        {
            id: "hil",
            name: qsTr("HIL Runner"),
            description: qsTr("Automated hardware-in-the-loop regression suites and fixture assertions"),
            icon: "flask-conical",
            hue: Theme.hueLime,
            category: "safety",
            tags: [qsTr("Fixtures"), qsTr("CI")]
        },
        {
            id: "trace",
            name: qsTr("ITM / ETM Trace"),
            description: qsTr("Cycle-accurate instruction profiling, PC sampling, and timing flamegraphs"),
            icon: "timer",
            hue: Theme.hueOrange,
            category: "debug",
            tags: [qsTr("ITM"), qsTr("Profiler")]
        }
    ]

    function byId(id) {
        if (!id) return null
        for (var i = 0; i < plugins.length; ++i) {
            if (plugins[i].id === id) return plugins[i]
        }
        return null
    }

    function categoryLabel(cat) {
        switch (String(cat || "").toLowerCase()) {
        case "hardware": return qsTr("Hardware")
        case "debug": return qsTr("Debug")
        case "safety": return qsTr("Safety")
        case "observability": return qsTr("Observability")
        default: return String(cat || "")
        }
    }

    function isEnabled(id) {
        var list = Settings.enabledPlugins
        if (list === undefined || list === null) {
            return byId(id) !== null
        }
        return list.indexOf(id) >= 0
    }

    function skillMeta(cmd) {
        var key = String(cmd || "").trim().split(/\s+/)[0].toLowerCase()
        switch (key) {
        case "/crash":
            return ({ hue: Theme.hueRose, icon: "bug" })
        case "/doctor":
            return ({ hue: Theme.hueAmber, icon: "waypoints" })
        case "/bringup":
            return ({ hue: Theme.hueEmerald, icon: "circuit-board" })
        case "/refactor":
            return ({ hue: Theme.hueViolet, icon: "wand-sparkles" })
        case "/hil":
            return ({ hue: Theme.hueCyan, icon: "flask-conical" })
        default:
            return ({ hue: Theme.hueIndigo, icon: "sparkles" })
        }
    }

    function riskHue(risk) {
        var r = String(risk || "").trim().toLowerCase()
        switch (r) {
        case "read_only":
            return Theme.hueCyan
        case "workspace_write":
            return Theme.hueViolet
        case "shell":
            return Theme.hueSky
        case "hardware_state":
            return Theme.hueAmber
        case "hardware_danger":
            return Theme.hueRose
        default:
            return Theme.hueCyan
        }
    }
}
