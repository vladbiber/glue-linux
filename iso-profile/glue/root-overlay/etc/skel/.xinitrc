#!/bin/sh
# Glue live: plain `startx` previews nvwm.
feh --no-fehbg --bg-fill /usr/share/backgrounds/glue/wallpaper.png &
exec nvwm
