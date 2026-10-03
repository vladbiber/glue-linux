#!/bin/sh
set -eu

if pacman -Q nvidia-open-dkms >/dev/null 2>&1 && \
   ! pacman -Q linux-cachyos-headers >/dev/null 2>&1; then
    echo "Actualizarea a fost oprită: nvidia-open-dkms are nevoie de linux-cachyos-headers." >&2
    exit 3
fi

if [ "${GLUE_HUB_DRY_RUN:-0}" = 1 ]; then
    echo 'pacman -Syu --noconfirm'
    exit 0
fi

exec pacman -Syu --noconfirm
