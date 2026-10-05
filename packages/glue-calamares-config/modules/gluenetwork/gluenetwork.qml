// Glue Linux: "Network" page (notesqml@gluenetwork).
// The install downloads packages, so this page checks the internet every few
// seconds and opens the Glue Network window (x-scheme-handler gluenetwork).
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import io.calamares.core 1.0

Item {
    id: page

    readonly property color colBg: "#FFFFFF"
    readonly property color colText: "#172033"
    readonly property color colMuted: "#5B6475"
    readonly property color colOk: "#2E7D4F"
    readonly property color colBad: "#B5484D"
    readonly property var probes: ["http://ping.archlinux.org/nm-check.txt",
                                   "https://mirror1.artixlinux.org/"]

    // unknown | online | offline
    property string state_: "unknown"
    property int pending: 0
    property bool anyOk: false

    function check() {
        if (pending > 0) return
        pending = probes.length
        anyOk = false
        for (var i = 0; i < probes.length; i++) probe(probes[i])
    }
    function probe(url) {
        var xhr = new XMLHttpRequest()
        var done = false
        var finish = function(ok) {
            if (done) return
            done = true
            if (ok) page.anyOk = true
            page.pending -= 1
            if (page.pending === 0) {
                page.state_ = page.anyOk ? "online" : "offline"
                Global.insert("glue_online", page.anyOk)
            }
        }
        xhr.onreadystatechange = function() {
            if (xhr.readyState === XMLHttpRequest.DONE)
                finish(xhr.status >= 200 && xhr.status < 400)
        }
        xhr.timeout = 4000
        xhr.ontimeout = function() { finish(false) }
        try {
            xhr.open("GET", url)
            xhr.send()
        } catch (e) {
            finish(false)
        }
    }

    Timer {
        interval: 3000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: page.check()
    }

    Rectangle { anchors.fill: parent; color: page.colBg }

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - 60, 560)
        spacing: 14

        Text {
            text: "Connect to the internet"
            color: page.colText
            font.pixelSize: 24
            font.bold: true
        }
        Text {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: page.colMuted
            font.pixelSize: 14
            text: "Glue Linux downloads the newest packages while it installs, so the installer needs a working internet connection."
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.topMargin: 8
            height: 76
            radius: 12
            color: page.state_ === "online" ? "#E8F4EC" : (page.state_ === "offline" ? "#F6E9EA" : "#F4F6F8")
            border.color: page.state_ === "online" ? page.colOk : (page.state_ === "offline" ? page.colBad : "#DDE1E6")
            border.width: 1

            RowLayout {
                anchors.fill: parent
                anchors.margins: 16
                spacing: 14
                Text {
                    text: page.state_ === "online" ? "✓" : (page.state_ === "offline" ? "✕" : "…")
                    color: page.state_ === "online" ? page.colOk : page.colBad
                    font.pixelSize: 30
                    font.bold: true
                }
                ColumnLayout {
                    spacing: 2
                    Layout.fillWidth: true
                    Text {
                        text: page.state_ === "online" ? "You are online"
                              : (page.state_ === "offline" ? "Not connected" : "Checking the connection…")
                        color: page.colText
                        font.pixelSize: 17
                        font.bold: true
                    }
                    Text {
                        Layout.fillWidth: true
                        wrapMode: Text.WordWrap
                        color: page.colMuted
                        font.pixelSize: 13
                        text: page.state_ === "online" ? "Everything is ready. Press Next."
                              : "Using a cable? Plug it in and it connects by itself. For Wi‑Fi, press the button below."
                    }
                }
            }
        }

        RowLayout {
            spacing: 10
            Button {
                text: "Connect to Wi‑Fi…"
                highlighted: page.state_ !== "online"
                onClicked: Qt.openUrlExternally("gluenetwork://open")
            }
            Button {
                text: "Check again"
                onClicked: page.check()
            }
        }

        Text {
            Layout.fillWidth: true
            Layout.topMargin: 6
            wrapMode: Text.WordWrap
            color: page.colMuted
            font.pixelSize: 12
            text: "You can press Next without internet, but the installation will stop at the download step."
        }
    }
}
