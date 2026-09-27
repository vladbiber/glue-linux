#!/usr/bin/env bash

# -----------------------------------------------------------------------------
# CACHING & MIGRATION
# -----------------------------------------------------------------------------
source "$(dirname "${BASH_SOURCE[0]}")/caching.sh"
qs_ensure_cache "workspaces"

# ============================================================================
# 1. ZOMBIE PREVENTION
# Kills any older instances of this script. When Quickshell reloads, 
# it can leave the old listener pipelines running in the background infinitely.
# ============================================================================
for pid in $(pgrep -f "workspaces.sh"); do
    if [ "$pid" != "$$" ] && [ "$pid" != "$PPID" ]; then
        kill -9 "$pid" 2>/dev/null
    fi
done

# Cleanly kill immediate children (like socat) when the script exits normally
cleanup() {
    pkill -P $$ 2>/dev/null
}
trap cleanup EXIT SIGTERM SIGINT

# --- Special Cleanup for Network/Bluetooth ---
# The network toggle starts a background bluetooth scan that must be killed explicitly.
BT_PID_FILE="$QS_RUN_WORKSPACES/bt_scan_pid"

if [ -f "$BT_PID_FILE" ]; then
    kill $(cat "$BT_PID_FILE") 2>/dev/null
    rm -f "$BT_PID_FILE"
fi

# Ensure bluetooth scan is explicitly turned off (timeout prevents deadlocks on fresh installs)
(timeout 2 bluetoothctl scan off > /dev/null 2>&1) &
# ---------------------------------------------

# Configuration: Parse from settings.json dynamically, fallback to 8
SETTINGS_FILE="$HOME/.config/hypr/settings.json"
SEQ_END=$(jq -r '.workspaceCount // 8' "$SETTINGS_FILE" 2>/dev/null)
# Double check it is a valid integer to prevent jq errors later
if ! [[ "$SEQ_END" =~ ^[0-9]+$ ]]; then
    SEQ_END=8
fi

is_mango() {
    command -v mmsg >/dev/null 2>&1 && [ -z "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]
}

print_mango_workspaces() {
    local tags_line occupied active urgent

    tags_line=$(timeout 2 mmsg -g -t 2>/dev/null | awk '/ tags / && $(NF-2) !~ /^0[0-9]/ && $(NF-1) !~ /^0[0-9]/ && $NF !~ /^0[0-9]/ { line=$0 } END { print line }')
    if [ -n "$tags_line" ]; then
        read -r occupied active urgent <<<"$(printf '%s\n' "$tags_line" | awk '{print $(NF-2), $(NF-1), $NF}')"
    fi

    if ! [[ "$occupied" =~ ^[0-9]+$ ]]; then occupied=0; fi
    if ! [[ "$active" =~ ^[0-9]+$ ]]; then active=1; fi
    if ! [[ "$urgent" =~ ^[0-9]+$ ]]; then urgent=0; fi

    {
        printf '['
        for ((i = 1; i <= SEQ_END; i++)); do
            mask=$((1 << (i - 1)))
            state="empty"
            if (( (active & mask) != 0 )); then
                state="active"
            elif (( (urgent & mask) != 0 )); then
                state="urgent"
            elif (( (occupied & mask) != 0 )); then
                state="occupied"
            fi
            [ "$i" -gt 1 ] && printf ','
            printf '{"id":%d,"state":"%s","tooltip":""}' "$i" "$state"
        done
        printf ']'
    } > "$QS_RUN_WORKSPACES/workspaces.tmp"

    mv "$QS_RUN_WORKSPACES/workspaces.tmp" "$QS_RUN_WORKSPACES/workspaces.json"
}

print_workspaces() {
    if is_mango; then
        print_mango_workspaces
        return
    fi

    # Get raw data with a timeout fallback
    spaces=$(timeout 2 hyprctl workspaces -j 2>/dev/null)
    active=$(timeout 2 hyprctl activeworkspace -j 2>/dev/null | jq '.id')

    # Failsafe if hyprctl crashes to prevent jq from outputting errors
    if [ -z "$spaces" ] || [ -z "$active" ]; then return; fi

    # Generate the JSON and write it atomically to prevent UI flickering
    echo "$spaces" | jq --unbuffered --argjson a "$active" --arg end "$SEQ_END" -c '
        # Create a map of workspace ID -> workspace data for easy lookup
        (map( { (.id|tostring): . } ) | add) as $s
        |
        # Iterate from 1 to SEQ_END
        [range(1; ($end|tonumber) + 1)] | map(
            . as $i |
            # Determine state: active -> occupied -> empty
            (if $i == $a then "active"
             elif ($s[$i|tostring] != null and $s[$i|tostring].windows > 0) then "occupied"
             else "empty" end) as $state |

            # Get window title for tooltip (if exists)
            (if $s[$i|tostring] != null then $s[$i|tostring].lastwindowtitle else "Empty" end) as $win |

            {
                id: $i,
                state: $state,
                tooltip: $win
            }
        )
    ' > "$QS_RUN_WORKSPACES/workspaces.tmp"
    
    mv "$QS_RUN_WORKSPACES/workspaces.tmp" "$QS_RUN_WORKSPACES/workspaces.json"
}

# Print initial state
print_workspaces

if is_mango; then
    while true; do
        timeout 3600 mmsg -w -t 2>/dev/null | while read -r line; do
            case "$line" in
                *" tags "*|tag\ *) print_workspaces ;;
            esac
        done
        sleep 1
        print_workspaces
    done
fi

# ============================================================================
# 2. THE EVENT DEBOUNCER
# Listen to Hyprland socket wrapped in an infinite loop
# ============================================================================
while true; do
    socat -u UNIX-CONNECT:$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket2.sock - | while read -r line; do
        case "$line" in
            workspace*|focusedmon*|activewindow*|createwindow*|closewindow*|movewindow*|destroyworkspace*)
                
                # -> THE FIX <-
                # Hyprland emits HUNDREDS of events a second when you move/resize windows.
                # This reads and discards all subsequent events arriving within a 50ms window.
                # It bundles the storm into a single UI update, completely preventing CPU clogging!
                while read -t 0.05 -r extra_line; do
                    continue
                done

                print_workspaces
                ;;
        esac
    done
    sleep 1
done
