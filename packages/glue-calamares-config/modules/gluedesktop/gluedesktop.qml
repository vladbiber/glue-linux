// Glue Linux: "Choose your desktop" page (notesqml@gluedesktop).
// Multi-select cards; the picked ids go to globalstorage as
// packagechooser_gluesessions (comma-separated), read by glueinstall.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import io.calamares.core 1.0
import "DesktopData.js" as Data

Item {
    id: page

    readonly property color colBg: "#FFFFFF"
    readonly property color colCard: "#F7F8FA"
    readonly property color colLine: "#DDE1E6"
    readonly property color colText: "#172033"
    readonly property color colMuted: "#5B6475"
    readonly property color colAccent: "#B5484D"
    readonly property color colAccentSoft: "#F6E9EA"
    readonly property string gsKey: "packagechooser_gluesessions"

    property var picked: initialPicked()
    property string notice: ""
    property int previewIndex: -1
    property int previewImage: 0

    function initialPicked() {
        var out = []
        for (var i = 0; i < Data.entries.length; i++)
            if (Data.entries[i].checked) out.push(Data.entries[i].id)
        return out
    }
    function isPicked(id) { return picked.indexOf(id) >= 0 }
    function save() { Global.insert(gsKey, picked.join(",")) }
    function toggle(id) {
        var next = picked.slice()
        var at = next.indexOf(id)
        if (at >= 0) {
            if (next.length === 1) {
                notice = "Keep at least one desktop picked."
                return
            }
            next.splice(at, 1)
        } else {
            next.push(id)
        }
        notice = ""
        picked = next
        save()
    }
    function dots(n) {
        var s = ""
        for (var i = 1; i <= 5; i++) s += i <= n ? "●" : "○"
        return s
    }

    Component.onCompleted: save()

    Rectangle { anchors.fill: parent; color: page.colBg }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 6

        Text {
            text: "Choose your desktop"
            color: page.colText
            font.pixelSize: 22
            font.bold: true
        }
        Text {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: page.colMuted
            font.pixelSize: 13
            text: "Pick one or more. Everything you pick is installed, and the login screen lets you choose which one to start. Click a picture to see it bigger."
        }
        Text {
            visible: page.notice !== ""
            text: page.notice
            color: page.colAccent
            font.pixelSize: 13
            font.bold: true
        }

        ScrollView {
            id: scroller
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 6
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            contentWidth: availableWidth

            Column {
                width: scroller.availableWidth
                spacing: 10

                Repeater {
                    model: Data.entries
                    delegate: Rectangle {
                        id: card
                        readonly property bool on: page.isPicked(modelData.id)
                        width: parent.width - 4
                        height: Math.max(150, info.implicitHeight + 24)
                        radius: 10
                        color: on ? page.colAccentSoft : page.colCard
                        border.color: on ? page.colAccent : page.colLine
                        border.width: on ? 2 : 1

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: page.toggle(modelData.id)
                        }

                        Rectangle {
                            id: thumb
                            x: 12; y: 12
                            width: 208; height: 126
                            radius: 6
                            color: "#E9ECEF"
                            clip: true
                            Image {
                                anchors.fill: parent
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                                source: modelData.images.length > 0 ? modelData.images[0] : ""
                            }
                            Text {
                                anchors.centerIn: parent
                                visible: modelData.images.length === 0
                                text: "No preview"
                                color: page.colMuted
                            }
                            Rectangle {
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.margins: 6
                                visible: modelData.images.length > 0
                                width: zoomText.implicitWidth + 12; height: 20; radius: 10
                                color: "#CC172033"
                                Text {
                                    id: zoomText
                                    anchors.centerIn: parent
                                    text: modelData.images.length > 1
                                          ? "View " + modelData.images.length + " pictures" : "View"
                                    color: "white"
                                    font.pixelSize: 11
                                }
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: modelData.images.length > 0
                                cursorShape: Qt.PointingHandCursor
                                onClicked: { page.previewImage = 0; page.previewIndex = index }
                            }
                        }

                        ColumnLayout {
                            id: info
                            anchors.left: thumb.right
                            anchors.leftMargin: 14
                            anchors.right: tick.left
                            anchors.rightMargin: 10
                            y: 10
                            spacing: 4

                            RowLayout {
                                spacing: 8
                                Text {
                                    text: modelData.name
                                    color: page.colText
                                    font.pixelSize: 17
                                    font.bold: true
                                }
                                Rectangle {
                                    height: 18; radius: 9
                                    width: kindText.implicitWidth + 14
                                    color: "#E4E8F7"
                                    Text {
                                        id: kindText
                                        anchors.centerIn: parent
                                        text: modelData.kind + (modelData.wayland ? " · Wayland" : " · X11")
                                        color: page.colText
                                        font.pixelSize: 11
                                    }
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                text: modelData.description
                                color: page.colText
                                font.pixelSize: 13
                            }
                            GridLayout {
                                columns: 3
                                columnSpacing: 10
                                rowSpacing: 0
                                Layout.topMargin: 2
                                Text { text: "Lightness"; color: page.colMuted; font.pixelSize: 12 }
                                Text { text: page.dots(modelData.lightness); color: page.colAccent; font.pixelSize: 13 }
                                Text {
                                    text: modelData.weight + (modelData.ram !== "" ? " · " + modelData.ram + " of RAM when idle" : "")
                                    color: page.colMuted; font.pixelSize: 12
                                }
                                Text { text: "Easy to use"; color: page.colMuted; font.pixelSize: 12 }
                                Text { text: page.dots(modelData.ease); color: page.colAccent; font.pixelSize: 13 }
                                Text {
                                    text: modelData.ease >= 5 ? "Works like Windows or macOS"
                                          : (modelData.ease >= 4 ? "Easy, with a few shortcuts to learn" : "Keyboard-driven, for tinkerers")
                                    color: page.colMuted; font.pixelSize: 12
                                }
                            }
                        }

                        Rectangle {
                            id: tick
                            anchors.right: parent.right
                            anchors.rightMargin: 14
                            anchors.verticalCenter: parent.verticalCenter
                            width: 26; height: 26; radius: 6
                            color: card.on ? page.colAccent : "white"
                            border.color: card.on ? page.colAccent : "#9AA3AF"
                            border.width: 2
                            Text {
                                anchors.centerIn: parent
                                visible: card.on
                                text: "✓"
                                color: "white"
                                font.pixelSize: 17
                                font.bold: true
                            }
                        }
                    }
                }
            }
        }

        Text {
            Layout.fillWidth: true
            elide: Text.ElideRight
            color: page.colMuted
            font.pixelSize: 12
            text: "Selected: " + page.picked.length
        }
    }

    // Big preview with all pictures of one desktop
    Rectangle {
        anchors.fill: parent
        visible: page.previewIndex >= 0
        color: "#E6101418"
        MouseArea { anchors.fill: parent; onClicked: page.previewIndex = -1 }

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 16
            spacing: 8
            readonly property var entry: page.previewIndex >= 0 ? Data.entries[page.previewIndex] : null

            RowLayout {
                Layout.fillWidth: true
                Text {
                    Layout.fillWidth: true
                    text: parent.parent.entry ? parent.parent.entry.name : ""
                    color: "white"; font.pixelSize: 18; font.bold: true
                }
                Button { text: "Close"; onClicked: page.previewIndex = -1 }
            }
            Image {
                Layout.fillWidth: true
                Layout.fillHeight: true
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                source: parent.entry && parent.entry.images.length > 0
                        ? parent.entry.images[page.previewImage % parent.entry.images.length] : ""
            }
            RowLayout {
                Layout.alignment: Qt.AlignHCenter
                visible: parent.entry !== null && parent.entry.images.length > 1
                spacing: 14
                Button {
                    text: "Previous"
                    onClicked: {
                        var n = parent.parent.entry.images.length
                        page.previewImage = (page.previewImage + n - 1) % n
                    }
                }
                Text {
                    color: "white"
                    text: parent.parent.entry ? (page.previewImage % parent.parent.entry.images.length + 1)
                                              + " / " + parent.parent.entry.images.length : ""
                }
                Button {
                    text: "Next"
                    onClicked: page.previewImage = (page.previewImage + 1) % parent.parent.entry.images.length
                }
            }
        }
    }
}
