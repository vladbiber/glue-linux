// Glue Bar — minimal amber Quickshell bar for Glue Linux.
// One bar per monitor: name on the left, clock centered, volume + battery on
// the right. Launch with:  qs -c glue-bar
import Quickshell
import Quickshell.Services.UPower
import Quickshell.Services.Pipewire
import QtQuick

ShellRoot {
    SystemClock {
        id: clock
        precision: SystemClock.Minutes
    }

    // keep the default audio sink's properties bound while the bar runs
    PwObjectTracker {
        objects: [Pipewire.defaultAudioSink]
    }

    Variants {
        model: Quickshell.screens

        PanelWindow {
            required property var modelData
            screen: modelData

            anchors {
                top: true
                left: true
                right: true
            }
            implicitHeight: 30
            color: "#100A02"

            Rectangle {
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                height: 1
                color: "#A66900"
            }

            Text {
                anchors.left: parent.left
                anchors.leftMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                text: "Glue"
                color: "#F1B00A"
                font.bold: true
                font.pixelSize: 13
            }

            Text {
                anchors.centerIn: parent
                text: Qt.formatDateTime(clock.date, "HH:mm   ddd d MMM")
                color: "#F1B00A"
                font.pixelSize: 13
            }

            Row {
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.verticalCenter: parent.verticalCenter
                spacing: 16

                Text {
                    visible: Pipewire.defaultAudioSink !== null
                    text: {
                        const audio = Pipewire.defaultAudioSink?.audio;
                        if (!audio)
                            return "";
                        return audio.muted
                            ? "vol muted"
                            : "vol " + Math.round(audio.volume * 100) + "%";
                    }
                    color: "#F1B00A"
                    font.pixelSize: 13
                }

                Text {
                    visible: UPower.displayDevice !== null
                             && UPower.displayDevice.isLaptopBattery
                    text: {
                        const device = UPower.displayDevice;
                        if (!device)
                            return "";
                        const pct = Math.round(device.percentage * 100) + "%";
                        return device.state === UPowerDeviceState.Charging
                            ? "bat " + pct + " +"
                            : "bat " + pct;
                    }
                    color: "#F1B00A"
                    font.pixelSize: 13
                }
            }
        }
    }
}
