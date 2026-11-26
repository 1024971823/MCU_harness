import QtQuick
import McuStudio

CodeBlock {
    id: root
    property var jsonValue: null

    language: "json"
    text: {
        if (jsonValue === null || jsonValue === undefined) return "{}"
        if (typeof jsonValue === "string") {
            var s = jsonValue.trim()
            if (s.length === 0) return "{}"
            try {
                return JSON.stringify(JSON.parse(s), null, 2)
            } catch (e) {
                return s
            }
        }
        try {
            return JSON.stringify(jsonValue, null, 2)
        } catch (e2) {
            return String(jsonValue)
        }
    }
}
