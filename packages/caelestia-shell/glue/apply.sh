#!/bin/sh
# apply.sh DIR - make the caelestia shell in DIR (the installed
# quickshell/caelestia tree) run on gluewc: our services/Hypr.qml replaces the
# Hyprland one, and the properties typed with Quickshell.Hyprland classes take
# the plain objects it hands out. Fails if upstream moved what it edits.
set -eu
dir=$1
here=$(dirname "$0")
cp "$here/services/Hypr.qml" "$dir/services/Hypr.qml"
files="modules/drawers/ContentWindow.qml
modules/windowinfo/WindowInfo.qml
modules/windowinfo/Details.qml
modules/windowinfo/Preview.qml
modules/bar/components/workspaces/Workspaces.qml
modules/bar/components/workspaces/Workspace.qml
modules/bar/components/workspaces/SpecialWorkspaces.qml"
for f in $files; do
    grep -q 'Hyprland\(Monitor\|Toplevel\|Workspace\)' "$dir/$f" || {
        echo "apply.sh: $f no longer names a Hyprland type" >&2; exit 1; }
    sed -i -E -e 's/property (HyprlandMonitor|HyprlandToplevel|HyprlandWorkspace) /property var /' \
        -e 's/property list<HyprlandToplevel> /property var /' "$dir/$f"
done
left=$(grep -rlE 'property (list<)?Hyprland(Monitor|Toplevel|Workspace)' "$dir" || true)
[ -z "$left" ] || { echo "apply.sh: typed Hyprland properties left in: $left" >&2; exit 1; }
# the bar exists before gluewc's state file is read: no monitor yet
grep -q 'monitor.activeWorkspace?.id' "$dir/modules/bar/components/workspaces/Workspaces.qml" || {
    echo "apply.sh: Workspaces.qml activeWsId line moved" >&2; exit 1; }
sed -i 's/monitor\.activeWorkspace?\.id/monitor?.activeWorkspace?.id/' \
    "$dir/modules/bar/components/workspaces/Workspaces.qml"
# the lock background: a screencopy of a locked gluewc output takes
# quickshell down (Qt Wayland, handleScreensChanged), the wallpaper does not
f=$dir/modules/lock/LockSurface.qml
grep -q 'sourceComponent: Config.lock.useWallpaper ? wallpaperBackground : screencopyBackground' "$f" || {
    echo "apply.sh: LockSurface background switch moved" >&2; exit 1; }
sed -i 's/sourceComponent: Config.lock.useWallpaper ? wallpaperBackground : screencopyBackground/sourceComponent: wallpaperBackground/' "$f"
