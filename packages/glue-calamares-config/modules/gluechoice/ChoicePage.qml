// Glue Linux: one-choice page with big cards (kernel, init, gaming).
// Writes packagechooser_<pageId> = the picked id, read by glueinstall.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import io.calamares.core 1.0
import "ChoiceData.js" as Data

Item {
    id: page
    property string pageId: ""
    readonly property var spec: Data.pages[pageId]
    property string picked: spec ? spec.default : ""

    readonly property color colBg: "#FFFFFF"
    readonly property color colCard: "#F7F8FA"
    readonly property color colLine: "#DDE1E6"
    readonly property color colText: "#172033"
    readonly property color colMuted: "#5B6475"
    readonly property color colAccent: "#B5484D"
    readonly property color colAccentSoft: "#F6E9EA"

    function pick(id) {
        picked = id
        Global.insert("packagechooser_" + pageId, id)
    }
    Component.onCompleted: if (spec) Global.insert("packagechooser_" + pageId, picked)

    Rectangle { anchors.fill: parent; color: page.colBg }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 6

        Text {
            text: page.spec ? page.spec.title : ""
            color: page.colText
            font.pixelSize: 22
            font.bold: true
        }
        Text {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            color: page.colMuted
            font.pixelSize: 13
            text: page.spec ? page.spec.subtitle : ""
        }

        ScrollView {
            id: scroller
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 8
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            contentWidth: availableWidth

            Column {
                width: scroller.availableWidth
                spacing: 10
                Repeater {
                    model: page.spec ? page.spec.items : []
                    delegate: Rectangle {
                        id: card
                        readonly property bool on: page.picked === modelData.id
                        width: parent.width - 4
                        height: body.implicitHeight + 28
                        radius: 10
                        color: on ? page.colAccentSoft : page.colCard
                        border.color: on ? page.colAccent : page.colLine
                        border.width: on ? 2 : 1

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: page.pick(modelData.id)
                        }

                        Rectangle {
                            id: radio
                            x: 16
                            y: 18
                            width: 22; height: 22; radius: 11
                            color: "white"
                            border.color: card.on ? page.colAccent : "#9AA3AF"
                            border.width: 2
                            Rectangle {
                                anchors.centerIn: parent
                                visible: card.on
                                width: 12; height: 12; radius: 6
                                color: page.colAccent
                            }
                        }

                        ColumnLayout {
                            id: body
                            anchors.left: radio.right
                            anchors.leftMargin: 14
                            anchors.right: parent.right
                            anchors.rightMargin: 16
                            y: 14
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
                                    visible: modelData.badge !== ""
                                    height: 18; radius: 9
                                    width: badgeText.implicitWidth + 14
                                    color: "#E4E8F7"
                                    Text {
                                        id: badgeText
                                        anchors.centerIn: parent
                                        text: modelData.badge
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
                            Repeater {
                                model: modelData.points
                                delegate: Text {
                                    Layout.fillWidth: true
                                    wrapMode: Text.WordWrap
                                    text: "•  " + modelData
                                    color: page.colMuted
                                    font.pixelSize: 13
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
