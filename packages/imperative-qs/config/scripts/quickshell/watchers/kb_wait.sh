#!/usr/bin/env bash
WHEATLEY_T0=$SECONDS
source "$(dirname "${BASH_SOURCE[0]}")/../../caching.sh"

PIPE="$QS_RUN_DIR/qs_kb_wait_$$.fifo"
mkfifo "$PIPE" 2>/dev/null
trap 'rm -f "$PIPE"; kill $(jobs -p) 2>/dev/null; exit 0' EXIT INT TERM

if [ -n "$HYPRLAND_INSTANCE_SIGNATURE" ]; then
    LC_ALL=C socat -U - UNIX-CONNECT:$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock 2>/dev/null | grep --line-buffered "activelayout>>" > "$PIPE" &
else
    sleep 10 > "$PIPE" &
fi

read -r _ < "$PIPE"
sleep 0.05

# Wheatley anti-spin guard: if the monitor above died instantly (missing
# tool, daemon not up yet, dead socket), this script would exit right away
# and the TopBar fetch->wait Process loop would respawn it in a tight loop
# (the historical 50%-CPU bug). Pace the loop to one spawn per 10s instead.
if [ "$((SECONDS - WHEATLEY_T0))" -lt 2 ]; then
    sleep 10
fi
