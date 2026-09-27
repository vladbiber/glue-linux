#!/usr/bin/env bash
if command -v hyprctl >/dev/null 2>&1 && [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then
    layout=$(LC_ALL=C hyprctl devices -j 2>/dev/null | jq -r '(.keyboards[] | select(.main == true) | .active_keymap) // .keyboards[0].active_keymap // empty' | head -n1)
elif command -v mmsg >/dev/null 2>&1; then
    layout=$(LC_ALL=C mmsg -g -k 2>/dev/null | awk '/kb_layout/ {print $2; found=1} END {if (!found) print ""}' | head -n1)
else
    layout=""
fi
[[ -z "$layout" || "$layout" == "null" ]] && layout="US"
echo "${layout:0:2}" | tr '[:lower:]' '[:upper:]'
