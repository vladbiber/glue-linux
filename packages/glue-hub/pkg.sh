#!/bin/sh
set -eu

action=${1-}
name=${2-}
case "$action" in install|remove) ;; *) echo "Acțiune invalidă." >&2; exit 2 ;; esac
case "$name" in
    ''|*[!A-Za-z0-9@._+:-]*) echo "Nume invalid." >&2; exit 2 ;;
esac

if [ "${GLUE_HUB_DRY_RUN:-0}" = 1 ]; then
    printf 'pacman %s %s\n' "$action" "$name"
    exit 0
fi

if [ "$action" = install ]; then
    exec pacman -S --needed --noconfirm "$name"
fi
exec pacman -Rns --noconfirm "$name"
