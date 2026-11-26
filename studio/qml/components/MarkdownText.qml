import QtQuick
import McuStudio

TextEdit {
    id: root
    property color linkColor: Theme.accent

    readOnly: true
    selectByMouse: true
    textFormat: TextEdit.MarkdownText
    wrapMode: TextEdit.Wrap
    font.family: Theme.fontSans
    font.pixelSize: Theme.textBase
    color: Theme.textPrimary
    selectionColor: Theme.accentSubtle
    selectedTextColor: Theme.textPrimary
    padding: 0

    onLinkActivated: function(link) {
        Utils.openExternalUrl(link)
    }
}
