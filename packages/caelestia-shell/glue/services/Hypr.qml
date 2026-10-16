pragma Singleton

import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// Glue Linux: the Hypr service of caelestia on gluewc instead of Hyprland.
// Same names and shapes the rest of the shell reads (monitors, workspaces,
// toplevels, lastIpcObject), built from what gluewc offers any shell:
//   $XDG_STATE_HOME/gluewc/workspaces  "name active workspace count,... layout"
//   foreign-toplevel                   the windows, their titles and app ids
//   gluewc-msg                         switching workspaces, screen power
// The objects are rebuilt on every change, so bindings that compare monitors
// (ShellState.forActive) see the same object through monitorFor and
// focusedMonitor. gluewc has no special workspaces and no Hyprland options:
// those parts answer empty.
Singleton {
    id: root

    readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state")) + "/gluewc"
    readonly property int workspaceCount: 9

    property var monitors: ({ values: [] })
    property var workspaces: ({ values: [] })
    readonly property var toplevels: ({ values: ToplevelManager.toplevels.values.map(t => root.wrapToplevel(t, null)) })
    readonly property bool usingLua: false

    readonly property var focusedMonitor: monitors.values.find(m => m.focused) ?? monitors.values[0] ?? null
    readonly property var focusedWorkspace: focusedMonitor?.activeWorkspace ?? null
    readonly property int activeWsId: focusedWorkspace?.id ?? 1
    readonly property var activeToplevel: ToplevelManager.activeToplevel ? wrapToplevel(ToplevelManager.activeToplevel, focusedWorkspace) : null

    readonly property var keyboard: null
    readonly property bool capsLock: false
    readonly property bool numLock: false
    readonly property string defaultKbLayout: "??"
    readonly property string kbLayoutFull: ""
    readonly property string kbLayout: "??"
    readonly property var kbMap: new Map()

    readonly property alias extras: extras
    readonly property var options: ({})
    readonly property var devices: ({ keyboards: [] })

    property string lastSpecialWorkspace: ""

    signal configReloaded

    function wrapToplevel(t: var, ws: var): var {
        return {
            title: t?.title ?? "",
            address: "",
            wayland: t,
            workspace: ws,
            lastIpcObject: {
                class: t?.appId ?? "",
                mapped: true,
                fullscreen: t?.fullscreen ? 2 : 0,
                tags: []
            }
        };
    }

    function parse(text: string): bool {
        // gluewc rewrites the file in place: keep the last full snapshot
        if (!text.trim() || !text.endsWith("\n"))
            return false;
        const mons = [];
        const wss = [];
        for (const line of text.split("\n")) {
            const f = line.trim().split(" ");
            if (f.length < 4)
                continue;
            const counts = f[3].split(",").map(x => parseInt(x) || 0);
            const mon = {
                name: f[0],
                id: mons.length,
                focused: f[1] === "1",
                activeWorkspace: null,
                lastIpcObject: { specialWorkspace: { name: "" } }
            };
            const active = (parseInt(f[2]) || 0) + 1;
            for (let i = 0; i < counts.length && i < root.workspaceCount; i++) {
                const id = i + 1;
                if (counts[i] === 0 && id !== active)
                    continue;
                const ws = {
                    id: id,
                    name: String(id),
                    monitor: mon,
                    lastIpcObject: { windows: counts[i] },
                    toplevels: { values: [] }
                };
                for (let n = 0; n < counts[i]; n++)
                    ws.toplevels.values.push(wrapToplevel(null, ws));
                if (id === active)
                    mon.activeWorkspace = ws;
                wss.push(ws);
            }
            mons.push(mon);
        }
        // ids repeat per output: the focused output's workspaces come first,
        // so a lookup by id finds the one the user is looking at
        wss.sort((a, b) => (b.monitor.focused ? 1 : 0) - (a.monitor.focused ? 1 : 0));
        monitors = { values: mons };
        workspaces = { values: wss };
        return mons.length > 0;
    }

    function msg(args: var): void {
        Quickshell.execDetached(["sh", "-c", "for m in gluewc-msg /usr/local/bin/gluewc-msg /usr/bin/gluewc-msg; do " + "command -v \"$m\" >/dev/null 2>&1 && exec \"$m\" \"$@\"; done", "caelestia"].concat(args));
    }

    function focusWorkspace(ws: var): void {
        const s = String(ws);
        let n = parseInt(s.replace(/^r/, ""));
        if (s.startsWith("r"))
            n = activeWsId + n;
        if (isNaN(n))
            return;
        n = Math.max(1, Math.min(root.workspaceCount, n));
        msg(["-o", focusedMonitor?.name ?? "", "workspace", String(n)].filter(a => a !== ""));
    }

    // The Hyprland dispatchers the shell sends, in either syntax, that gluewc
    // can do; the rest are dropped.
    function dispatch(request: string): void {
        let m = request.match(/^workspace\s+(\S+)$/) || request.match(/^hl\.dsp\.focus\(\{\s*workspace\s*=\s*"([^"]+)"/);
        if (m) {
            focusWorkspace(m[1]);
            return;
        }
        if (/dpms off|action = "disable"/.test(request))
            msg(["power", "off"]);
        else if (/dpms on|action = "enable"/.test(request))
            msg(["power", "on"]);
    }

    function toggleSpecial(name: string): void {
    }

    function cycleSpecialWorkspace(direction: string): void {
    }

    function monitorNames(): list<string> {
        return monitors.values.map(e => e.name);
    }

    function monitorFor(screen: ShellScreen): var {
        if (!screen)
            return null;
        return monitors.values.find(m => m.name === screen.name) ?? (monitors.values.length === 1 ? monitors.values[0] : null);
    }

    function trimWsName(name: string): string {
        return name.startsWith("special:") ? name.slice("special:".length) : name;
    }

    function toplevelsForWs(ws: int, ignoredTags = []): var {
        return workspaces.values.find(w => w.id === ws)?.toplevels.values ?? [];
    }

    function isToplevelIgnored(toplevel: var, ignoredTags = []): bool {
        return !toplevel?.lastIpcObject?.mapped;
    }

    function reloadDynamicConfs(): void {
    }

    property int readRetries: 0

    Timer {
        id: readDelay

        interval: 35
        onTriggered: stateFile.reload()
    }

    FileView {
        id: stateFile

        path: root.stateDir + "/workspaces"
        watchChanges: true
        preload: true
        printErrors: false
        onFileChanged: {
            root.readRetries = 0;
            readDelay.restart();
        }
        onLoaded: {
            if (!root.parse(text()) && root.readRetries++ < 3)
                readDelay.restart();
        }
        onLoadFailed: {
            if (root.readRetries++ < 50)
                readDelay.restart();
        }
    }

    IpcHandler {
        function refreshDevices(): void {
        }

        function cycleSpecialWorkspace(direction: string): void {
        }

        function listSpecialWorkspaces(): string {
            return "";
        }

        target: "hypr"
    }

    QtObject {
        id: extras

        readonly property var options: ({})
        readonly property var devices: ({ keyboards: [] })

        function refreshDevices(): void {
        }

        function refreshOptions(): void {
        }

        function applyOptions(opts: var): void {
        }

        function message(m: string): void {
        }

        function batchMessage(ms: var): void {
        }
    }
}
