#!/usr/bin/env bash
WHEATLEY_T0=$SECONDS
source "$(dirname "${BASH_SOURCE[0]}")/../../caching.sh"

# Wheatley failsafe: without pactl (libpulse) — or before pipewire-pulse is
# up — `pactl subscribe` exits instantly, and the TopBar fetch->wait Process
# loop respawns this script nonstop (burns a whole core). Poll slowly instead.
if ! command -v pactl >/dev/null 2>&1; then
    sleep 10
    exit 0
fi

PIPE="$QS_RUN_DIR/qs_audio_wait_$$.fifo"
mkfifo "$PIPE" 2>/dev/null

trap 'rm -f "$PIPE"; kill $MONITOR_PID 2>/dev/null; exit 0' EXIT INT TERM

# Run pactl isolated and capture its exact PID to prevent PipeWire connection exhaustion
LC_ALL=C pactl subscribe 2>/dev/null > "$PIPE" &
MONITOR_PID=$!

# Failsafe timeout mirrors battery_wait.sh: if pactl dies immediately (no
# pulse server yet), this still costs one spawn per 10s, not a tight loop.
if ! timeout 600 grep -m 1 -E "sink|server" < "$PIPE" > /dev/null; then
    sleep 10
fi

# Wheatley anti-spin guard: if the monitor above died instantly (missing
# tool, daemon not up yet, dead socket), this script would exit right away
# and the TopBar fetch->wait Process loop would respawn it in a tight loop
# (the historical 50%-CPU bug). Pace the loop to one spawn per 10s instead.
if [ "$((SECONDS - WHEATLEY_T0))" -lt 2 ]; then
    sleep 10
fi
