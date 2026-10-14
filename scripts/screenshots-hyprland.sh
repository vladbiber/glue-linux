#!/bin/bash
# screenshots-hyprland.sh - real 1920x1080 catalog screenshots of the two
# Hyprland sessions (caelestia, ii), taken headless inside the build container.
#
#   sh scripts/screenshots-hyprland.sh [caelestia|ii|all]
#
# Host side: one `docker run --rm --privileged` of glue-build per target, the
# repo on /glue. Packages come from .glue-repo (the last build) or, when
# GLUE_HYPR_PKGS names a directory, from the *.pkg.tar.zst files in it.
#
# Inside: Hyprland refuses to run as root, so the session runs as `builder`.
# Its nested Wayland backend needs a parent compositor: a headless gluewc on
# wayland-0. The nested window gets no monitor in that setup, so a headless
# Hyprland output is created and the shell restarted on it (on real hardware
# the monitors exist before the shell starts). Each picture is a plain grim
# capture of Hyprland's output; menus are opened through the shells' own IPC.
#
# Output: packages/glue-installer/catalog/screenshots/hyprland-<target>-*.png
set -eu

WIDTH=1920
HEIGHT=1080
MIN_COLORS=1000
IMAGE="${GLUE_SCREENSHOT_IMAGE:-glue-build}"
WALLPAPER=/usr/share/backgrounds/glue/wallpaper.png
OUT_REL=packages/glue-installer/catalog/screenshots

log() { printf '[hypr-shots] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

files_for() {
    case "$1" in
        caelestia) echo hyprland-caelestia-desktop.png hyprland-caelestia-launcher.png \
                        hyprland-caelestia-dashboard.png hyprland-caelestia-sidebar.png \
                        hyprland-caelestia-session.png ;;
        ii)        echo hyprland-ii-desktop.png hyprland-ii-search.png hyprland-ii-overview.png \
                        hyprland-ii-sidebar-left.png hyprland-ii-sidebar-right.png \
                        hyprland-ii-cheatsheet.png ;;
        *) die "unknown target '$1' (caelestia|ii|all)" ;;
    esac
}

verify_png() {
    local png=$1 w h k
    [ -s "$png" ] || die "$png missing or empty"
    set -- $(magick identify -format '%w %h %k\n' "$png" | head -n1)
    w=${1:-0}; h=${2:-0}; k=${3:-0}
    log "$png: ${w}x${h}, $k colours, $(stat -c %s "$png") bytes"
    [ "$w" = "$WIDTH" ] && [ "$h" = "$HEIGHT" ] || die "$png is ${w}x${h}"
    [ "$k" -gt "$MIN_COLORS" ] || die "$png has only $k colours (blank capture?)"
}

# ---------------------------------------------------------------- host ----
host_main() {
    local target=$1 targets t root pkgs=""
    root=$(cd -- "$(dirname -- "$0")/.." && pwd)
    command -v docker >/dev/null || die "docker not found"
    command -v magick >/dev/null || die "imagemagick (magick) not found on the host"
    [ -e /dev/dri/renderD128 ] || die "/dev/dri/renderD128 missing"
    case "$target" in all) targets="caelestia ii" ;; *) files_for "$target" >/dev/null; targets=$target ;; esac
    [ -n "${GLUE_HYPR_PKGS:-}" ] && pkgs="-v $(cd "$GLUE_HYPR_PKGS" && pwd):/hypr-pkgs:ro"
    for t in $targets; do
        log "=== $t"
        docker run --rm --privileged --network host --entrypoint /bin/bash \
            -v /dev/dri:/dev/dri -v "$root:/glue" $pkgs -w /glue \
            -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
            "$IMAGE" /glue/scripts/screenshots-hyprland.sh --inside "$t"
        for f in $(files_for "$t"); do verify_png "$root/$OUT_REL/$f"; done
    done
}

# ------------------------------------------------------------- container ----
pkg_file() { # newest package file for NAME from /hypr-pkgs or .glue-repo
    ls -1t /hypr-pkgs/"$1"-[0-9r]*.pkg.tar.zst /glue/.glue-repo/"$1"-[0-9r]*.pkg.tar.zst \
        2>/dev/null | head -n1
}

inside_main() {
    local target=$1 out=/glue/$OUT_REL names f list=""
    grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
    pacman-key --add /glue/packages/glue-keyring/glue.gpg >/dev/null 2>&1
    pacman-key --lsign-key "$(gpg --show-keys --with-colons /glue/packages/glue-keyring/glue.gpg |
        awk -F: '/^fpr/{print $10; exit}')" >/dev/null 2>&1
    pacman -Sy --noconfirm >/dev/null
    names="gluewc glue-branding glue-hyprland ttf-material-symbols-variable ttf-rubik-vf swappy matugen"
    case "$target" in
        caelestia) names="$names libcava qt6-m3shapes python-materialyoucolor caelestia-cli caelestia-shell" ;;
        ii) names="$names ttf-google-sans-flex ttf-readex-pro otf-space-grotesk wlogout ii-vynx" ;;
    esac
    for n in $names; do
        f=$(pkg_file "$n"); [ -n "$f" ] || die "no package file for $n"
        list="$list $f"
    done
    # shellcheck disable=SC2086
    pacman -S --noconfirm --needed alacritty fastfetch firefox imagemagick grim \
        dbus ttf-dejavu ttf-liberation libnotify wtype >/dev/null
    pacman -U --noconfirm --needed $list >/dev/null
    ln -sf ../usr/lib/os-release /etc/os-release
    echo glue-live > /proc/sys/kernel/hostname 2>/dev/null || true
    for f in $(files_for "$target"); do rm -f "$out/$f"; done
    install -d -o builder /tmp/shots
    sudo -u builder env HOME=/home/builder dbus-run-session -- \
        /bin/bash /glue/scripts/screenshots-hyprland.sh --session "$target"
    for f in $(files_for "$target"); do
        cp "/tmp/shots/$f" "$out/$f"
        chown "${HOST_UID:-0}:${HOST_GID:-0}" "$out/$f"
    done
}

# ------------------------------------------------- session (as builder) ----
poll() { # poll SECONDS what cmd...
    local secs=$1 what=$2 i=0; shift 2
    while [ "$i" -lt $((secs * 5)) ]; do
        "$@" >/dev/null 2>&1 && return 0
        sleep 0.2; i=$((i + 1))
    done
    die "timed out after ${secs}s waiting for $what"
}

shot() { # shot NAME [settle seconds]
    sleep "${2:-3}"
    grim "/tmp/shots/$1" || die "grim failed for $1"
    log "captured $1"
}

clients() { hyprctl -j clients 2>/dev/null | grep -c '"class"'; }

session_main() {
    local target=$1 L=$HOME/log
    export XDG_RUNTIME_DIR=/tmp/xr-shots
    rm -rf "$XDG_RUNTIME_DIR"; mkdir -m 700 "$XDG_RUNTIME_DIR"; mkdir -p "$L" "$HOME/.local/share"
    trap 'kill $(jobs -p) 2>/dev/null' EXIT
    mkdir -p "$HOME/.config/gluewc" "$HOME/.config/fastfetch"
    cp /usr/share/gluewc/config.def.conf "$HOME/.config/gluewc/config.conf"
    cp /etc/glue/fastfetch.jsonc "$HOME/.config/fastfetch/config.jsonc"
    WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128 \
        gluewc >"$L/gluewc.log" 2>&1 &
    poll 30 "gluewc" test -S "$XDG_RUNTIME_DIR/wayland-0"
    WAYLAND_DISPLAY=wayland-0 XCURSOR_THEME=Adwaita XCURSOR_SIZE=24 GLUE_SESSION="hyprland-$target" \
        glue-hyprland "$target" >"$L/hyprland.log" 2>&1 &
    poll 60 "Hyprland" sh -c 'ls "$XDG_RUNTIME_DIR"/hypr/*/.socket.sock'
    HYPRLAND_INSTANCE_SIGNATURE=$(ls "$XDG_RUNTIME_DIR/hypr" | head -n1)
    export HYPRLAND_INSTANCE_SIGNATURE WAYLAND_DISPLAY=wayland-1
    poll 30 "the Hyprland socket" test -S "$XDG_RUNTIME_DIR/wayland-1"
    hyprctl output create headless GLUE-1 >/dev/null
    hyprctl keyword monitor "GLUE-1,${WIDTH}x${HEIGHT}@60,0x0,1" >/dev/null
    sleep 3
    # the shell came up before the output: start it again on it
    for p in $(pgrep -u "$(id -u)" -x qs); do kill "$p"; done
    sleep 1
    case "$target" in
        caelestia) caelestia_session ;;
        ii) ii_session ;;
    esac
}

launch_clients() {
    alacritty -e bash -c 'fastfetch; exec bash --norc' >/dev/null 2>&1 &
    sleep 2
    firefox --no-remote --new-instance --profile /tmp/ff --new-window file:///tmp/ff/glue.html \
        >/dev/null 2>&1 &
}

write_firefox_profile() {
    mkdir -p /tmp/ff
    cat > /tmp/ff/user.js <<'EOF'
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("toolkit.telemetry.reportingpolicy.firstRun", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("app.update.enabled", false);
user_pref("browser.startup.page", 0);
EOF
    cat > /tmp/ff/glue.html <<'EOF'
<!doctype html>
<html><head><meta charset="utf-8"><title>Glue Linux</title>
<style>
 body{margin:0;background:#F4F6F8;color:#172033;font-family:sans-serif;
      display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh}
 h1{font-size:64px;margin:0 0 16px}
 p{color:#5B6475;font-size:24px;margin:4px}
 hr{width:40%;border:0;border-top:3px solid #B5484D;margin:24px 0}
</style></head>
<body><h1>Glue Linux</h1><hr>
<p>Light. Fast. Yours.</p>
<p>Welcome to your new desktop.</p>
</body></html>
EOF
}

caelestia_session() {
    local q="qs -c caelestia ipc call"
    caelestia shell -d >"$HOME/log/shell.log" 2>&1
    poll 60 "caelestia IPC" qs -c caelestia ipc show
    caelestia wallpaper -f "$WALLPAPER"
    sleep 6
    write_firefox_profile
    launch_clients
    poll 90 "two windows" sh -c '[ "$(hyprctl -j clients | grep -c "\"class\"")" -ge 2 ]'
    shot hyprland-caelestia-desktop.png 10
    $q drawers toggle launcher; sleep 1; wtype fi 2>/dev/null || true
    shot hyprland-caelestia-launcher.png 3
    $q drawers toggle launcher
    $q drawers toggle dashboard
    shot hyprland-caelestia-dashboard.png 3
    $q drawers toggle dashboard
    notify-send -a Glue "Welcome to Glue" "Your Hyprland desktop with Caelestia is ready." || true
    $q drawers toggle sidebar
    shot hyprland-caelestia-sidebar.png 3
    $q drawers toggle sidebar
    $q drawers toggle session
    shot hyprland-caelestia-session.png 3
    $q drawers toggle session
}

ii_session() {
    local q="qs -c ii ipc call"
    export ILLOGICAL_IMPULSE_VIRTUAL_ENV=/usr/lib/ii-vynx/venv
    (MALLOC_CONF=narenas:1 qs -c ii -d >"$HOME/log/shell.log" 2>&1)
    poll 60 "ii IPC" qs -c ii ipc show
    sleep 10
    "$HOME/.config/quickshell/ii/scripts/colors/switchwall.sh" --image "$WALLPAPER" >/dev/null 2>&1 || true
    sleep 6
    write_firefox_profile
    launch_clients
    poll 90 "two windows" sh -c '[ "$(hyprctl -j clients | grep -c "\"class\"")" -ge 2 ]'
    shot hyprland-ii-desktop.png 10
    $q search open; sleep 1; $q search setQuery fire
    shot hyprland-ii-search.png 3
    $q search close
    $q search workspacesToggle
    shot hyprland-ii-overview.png 3
    $q search close
    $q sidebarLeft open
    shot hyprland-ii-sidebar-left.png 3
    $q sidebarLeft close
    notify-send -a Glue "Welcome to Glue" "Your Hyprland desktop with ii is ready." || true
    $q sidebarRight open
    shot hyprland-ii-sidebar-right.png 3
    $q sidebarRight close
    # the keybind pages are built on first open: give them time
    $q cheatsheet openTab keybinds
    shot hyprland-ii-cheatsheet.png 10
    $q cheatsheet close
}

case "${1:-all}" in
    --inside)  inside_main "${2:?target}" ;;
    --session) session_main "${2:?target}" ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *)         host_main "${1:-all}" ;;
esac
