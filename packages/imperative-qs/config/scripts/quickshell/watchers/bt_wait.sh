#!/usr/bin/env bash
WHEATLEY_T0=$SECONDS
source "$(dirname "${BASH_SOURCE[0]}")/../../caching.sh"

PIPE="$QS_RUN_DIR/qs_bt_wait_$$.fifo"
mkfifo "$PIPE" 2>/dev/null
trap 'rm -f "$PIPE"; kill $(jobs -p) 2>/dev/null; exit 0' EXIT INT TERM
LC_ALL=C dbus-monitor --system "type='signal',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged',arg0='org.bluez.Device1'" 2>/dev/null | grep --line-buffered 'string "Connected"' > "$PIPE" &
LC_ALL=C dbus-monitor --system "type='signal',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged',arg0='org.bluez.Adapter1'" 2>/dev/null | grep --line-buffered 'string "Powered"' > "$PIPE" &
read -r _ < "$PIPE"

# Wheatley anti-spin guard: if the monitor above died instantly (missing
# tool, daemon not up yet, dead socket), this script would exit right away
# and the TopBar fetch->wait Process loop would respawn it in a tight loop
# (the historical 50%-CPU bug). Pace the loop to one spawn per 10s instead.
if [ "$((SECONDS - WHEATLEY_T0))" -lt 2 ]; then
    sleep 10
fi
