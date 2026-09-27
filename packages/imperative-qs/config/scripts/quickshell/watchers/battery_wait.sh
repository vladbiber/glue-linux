#!/usr/bin/env bash
WHEATLEY_T0=$SECONDS
source "$(dirname "${BASH_SOURCE[0]}")/../../caching.sh"

PIPE="$QS_RUN_DIR/qs_battery_wait_$$.fifo"
mkfifo "$PIPE" 2>/dev/null

trap 'rm -f "$PIPE"; kill $MONITOR_PID 2>/dev/null; exit 0' EXIT INT TERM

# Run udevadm isolated and capture its exact PID
LC_ALL=C udevadm monitor --subsystem-match=power_supply 2>/dev/null > "$PIPE" &
MONITOR_PID=$!

# Blocks until udevadm catches a change, OR 30 seconds pass (your failsafe).
# Either way, when this line finishes, the trap fires and cleans up perfectly.
timeout 10 grep -m 1 "change" < "$PIPE" > /dev/null

# Wheatley anti-spin guard: if the monitor above died instantly (missing
# tool, daemon not up yet, dead socket), this script would exit right away
# and the TopBar fetch->wait Process loop would respawn it in a tight loop
# (the historical 50%-CPU bug). Pace the loop to one spawn per 10s instead.
if [ "$((SECONDS - WHEATLEY_T0))" -lt 2 ]; then
    sleep 10
fi
