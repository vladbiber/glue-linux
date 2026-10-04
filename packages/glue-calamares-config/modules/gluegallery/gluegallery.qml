// Glue Linux: "Preview the desktops" page (roadmap 5.6b lot 3).
// Informational only: the packagechooser pages own the choices. The root is a
// plain Item (no calamares imports needed), so the file also loads in a bare
// qml6 run. Previous/Next WRAP at the ends (see GalleryNav.js).
import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import "GalleryData.js" as Data
import "GalleryNav.js" as Nav

Item {
    id: page

    // Palette: bg 100A02, lines A66900, text F1B00A.
    readonly property color colBg: "#100A02"
    readonly property color colLines: "#A66900"
    readonly property color colText: "#F1B00A"

    property int entryIndex: 0
    property int imageIndex: 0
    readonly property var entry: Data.entries.length > 0 ? Data.entries[entryIndex] : null
    readonly property int count: Nav.imageCount(entry)
    // Image failed to load: behave as if the entry had no preview.
    property bool imageFailed: false
    readonly property bool hasImage: count > 0 && !imageFailed
    readonly property string indicatorText: indicator.text   // "N/M" or "0/0"

    onEntryIndexChanged: { imageIndex = 0; imageFailed = false }
    onImageIndexChanged: imageFailed = false

    Rectangle { anchors.fill: parent; color: page.colBg }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 16
        spacing: 10

        Text {
            text: "Preview the desktops"
            color: page.colText
            font.pixelSize: 22
            font.bold: true
        }

        Flow {
            Layout.fillWidth: true
            spacing: 6
            Repeater {
                model: Data.entries
                delegate: Button {
                    text: Nav.tabLabel(modelData)
                    checkable: true
                    checked: index === page.entryIndex
                    onClicked: page.entryIndex = index
                    contentItem: Text {
                        text: parent.text
                        color: page.colText
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    background: Rectangle {
                        color: parent.checked ? page.colLines : page.colBg
                        border.color: page.colLines
                        border.width: 1
                        radius: 4
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: page.colBg
            border.color: page.colLines
            border.width: 1

            Image {
                id: shot
                anchors.fill: parent
                anchors.margins: 2
                visible: page.hasImage
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                source: page.count > 0 ? page.entry.images[page.imageIndex] : ""
                onStatusChanged: if (status === Image.Error) page.imageFailed = true
            }

            Text {
                anchors.centerIn: parent
                visible: !page.hasImage
                text: "No preview available"
                color: page.colText
                font.pixelSize: 18
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Button {
                text: "Previous"
                enabled: page.count > 1
                onClicked: page.imageIndex = Nav.prev(page.imageIndex, page.count)
            }
            Text {
                id: indicator
                Layout.fillWidth: true
                horizontalAlignment: Text.AlignHCenter
                color: page.colText
                text: Nav.label(page.imageIndex, page.hasImage ? page.count : 0)
            }
            Button {
                text: "Next"
                enabled: page.count > 1
                onClicked: page.imageIndex = Nav.next(page.imageIndex, page.count)
            }
        }
    }
}
