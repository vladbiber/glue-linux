#!/bin/bash
# greeter-headless.sh — capture the real Glue ReGreet screen in a headless
# Cage session. The host entry point uses a disposable privileged build
# container; --inside and --session are implementation details.
set -eu

WIDTH=1920
HEIGHT=1080
MIN_COLORS=100
TIMEOUT=45
IMAGE="${GLUE_GREETER_IMAGE:-glue-pkgbuild-img}"

log() { printf '[greeter] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

verify_png() {
    local png=$1 w h colors kind
    [ -s "$png" ] || die "$png is missing or empty"
    kind=$(file -b "$png")
    case "$kind" in PNG\ image\ data*) ;; *) die "$png is not a PNG ($kind)" ;; esac
    set -- $(magick identify -format '%w %h %k' "$png")
    w=${1:-0}; h=${2:-0}; colors=${3:-0}
    log "$png: ${w}x${h}, $colors unique colours, $(stat -c %s "$png") bytes"
    [ "$w" = "$WIDTH" ] && [ "$h" = "$HEIGHT" ] || \
        die "$png is ${w}x${h}, expected ${WIDTH}x${HEIGHT}"
    [ "$colors" -ge "$MIN_COLORS" ] || \
        die "$png has only $colors unique colours (likely a blank capture)"
}

host_main() {
    local root png
    root=$(cd -- "$(dirname -- "$0")/.." && pwd)
    png="$root/screenshots/greeter.png"
    command -v docker >/dev/null 2>&1 || die "docker is not installed"
    command -v magick >/dev/null 2>&1 || die "ImageMagick (magick) is not installed"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || \
        die "Docker image $IMAGE is missing"
    mkdir -p "$root/screenshots"
    rm -f "$png"
    log "starting disposable privileged container from $IMAGE"
    docker run --rm --privileged --network host \
        -v "$root:/glue" -w /glue \
        -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        "$IMAGE" /bin/bash /glue/scripts/greeter-headless.sh --inside
    verify_png "$png"
}

generate_profile() {
    PYTHONPATH=/glue/packages/glue-installer python - <<'PY'
from pathlib import Path

from glue_installer.catalog import load_catalog
from glue_installer.plan_greeter import _greeter_files

catalog = load_catalog(Path("/glue/packages/glue-installer/catalog/catalog.json"))
files = _greeter_files(catalog.sessions, {}, {})
for planned in files:
    # This capture needs only the greeter profile and authoritative session
    # entries. Avoid installing login wrappers and portal files into the
    # disposable container when they cannot be exercised by --demo.
    if not (planned.path.startswith("/etc/greetd/") or
            planned.path.startswith("/usr/share/glue/sessions/")):
        continue
    target = Path(planned.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(planned.content, encoding="utf-8")
    target.chmod(planned.mode)
PY
}

ensure_cage() {
    local pkg
    if command -v cage >/dev/null 2>&1 && \
            cage -v 2>&1 | grep -q '^Cage version 0\.3\.1'; then
        return
    fi
    pkg=$(find /glue/packages/cage /home/builder/pkgs -maxdepth 1 \
        -name 'cage-0.3.1-*.pkg.tar.*' -print 2>/dev/null | head -n1 || true)
    if [ -z "$pkg" ]; then
        log "building Cage 0.3.1 from packages/cage"
        rm -rf /tmp/build-cage
        cp -r /glue/packages/cage /tmp/build-cage
        chown -R builder:builder /tmp/build-cage
        (cd /tmp/build-cage && sudo -u builder \
            makepkg -f --syncdeps --noconfirm --skippgpcheck)
        pkg=$(find /tmp/build-cage -maxdepth 1 -name 'cage-0.3.1-*.pkg.tar.*' \
            -print | head -n1)
    fi
    pacman -U --noconfirm "$pkg"
}

inside_main() {
    local png=/glue/screenshots/greeter.png
    [ -w /glue ] || die "/glue is not writable"
    grep -q '^DisableSandbox' /etc/pacman.conf || \
        sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
    log "installing ReGreet and headless capture dependencies"
    pacman -Sy --noconfirm
    pacman -S --noconfirm --needed greetd-regreet accountsservice dbus grim imagemagick \
        wlr-randr strace cantarell-fonts adwaita-icon-theme
    ensure_cage
    [ "$(cage -v 2>&1 | sed -n 's/^Cage version \([0-9][0-9.]*\).*/\1/p')" = 0.3.1 ] || \
        die "Cage 0.3.1 is required ($(cage -v 2>&1))"
    [ "$(regreet --version)" = "regreet 0.5.0" ] || \
        die "ReGreet 0.5.0 is required ($(regreet --version))"
    install -Dm644 /glue/packages/glue-branding/wallpaper.png \
        /usr/share/backgrounds/glue/wallpaper.png
    generate_profile
    mkdir -p /glue/screenshots
    rm -f "$png"
    dbus-run-session -- /bin/bash /glue/scripts/greeter-headless.sh --session "$png"
    verify_png "$png"
    chown "${HOST_UID:-0}:${HOST_GID:-0}" "$png"
}

poll() {
    local seconds=$1 description=$2; shift 2
    local attempt=0 max=$((seconds * 5))
    while [ "$attempt" -lt "$max" ]; do
        "$@" >/dev/null 2>&1 && return 0
        sleep 0.2
        attempt=$((attempt + 1))
    done
    die "timed out after ${seconds}s waiting for $description"
}

wayland_ready() {
    local socket
    for socket in "$XDG_RUNTIME_DIR"/wayland-*; do
        case "$socket" in *.lock) continue ;; esac
        [ -S "$socket" ] || continue
        WAYLAND_DISPLAY=${socket##*/}
        export WAYLAND_DISPLAY
        return 0
    done
    return 1
}

regreet_under_cage() {
    local pid parent
    for pid in $(pgrep -x regreet 2>/dev/null || true); do
        parent=$pid
        while [ "$parent" -gt 1 ] 2>/dev/null; do
            parent=$(ps -o ppid= -p "$parent" 2>/dev/null | tr -d ' ')
            [ -n "$parent" ] || break
            [ "$parent" = "$CAGE_PID" ] && return 0
        done
    done
    return 1
}

sessions_loaded() {
    local desktop kind overlay
    for desktop in /usr/share/glue/sessions/*.desktop; do
        [ -f "$desktop" ] || return 1
        kind=wayland-sessions
        grep -qx 'X-Glue-SessionType=x11' "$desktop" && kind=xsessions
        overlay="$SESSION_DATA/$kind/${desktop##*/}"
        [ "$(readlink -f "$overlay")" = "$desktop" ] || return 1
        grep -F "$overlay" /tmp/regreet.strace >/dev/null 2>&1 || return 1
    done
}

cleanup_session() {
    set +e
    [ -n "${CAGE_PID:-}" ] && kill "$CAGE_PID" 2>/dev/null
    sleep 1
    [ -n "${CAGE_PID:-}" ] && kill -9 "$CAGE_PID" 2>/dev/null
    [ -n "${ACCOUNTSD_PID:-}" ] && kill "$ACCOUNTSD_PID" 2>/dev/null
    [ -n "${POLKITD_PID:-}" ] && kill "$POLKITD_PID" 2>/dev/null
    [ -n "${SYSTEM_DBUS_PID:-}" ] && kill "$SYSTEM_DBUS_PID" 2>/dev/null
}

session_main() {
    local png=$1 rc=0 expected session_data desktop kind shared base
    export HOME=/root
    export XDG_RUNTIME_DIR=/tmp/glue-greeter-runtime
    export XDG_SESSION_TYPE=wayland
    export XDG_CURRENT_DESKTOP=cage
    export GTK_USE_PORTAL=0
    export GDK_DEBUG=no-portals
    export GTK_A11Y=none
    export GSK_RENDERER=cairo
    export WLR_BACKENDS=headless
    export WLR_HEADLESS_OUTPUTS=1
    export WLR_LIBINPUT_NO_DEVICES=1
    export WLR_RENDERER=pixman
    export WIDTH HEIGHT
    rm -rf "$XDG_RUNTIME_DIR"
    install -d -m 700 "$XDG_RUNTIME_DIR"
    session_data="$XDG_RUNTIME_DIR/glue-session-data"
    SESSION_DATA=$session_data
    export SESSION_DATA
    install -d -m 700 "$session_data/xsessions" "$session_data/wayland-sessions"
    for desktop in /usr/share/glue/sessions/*.desktop; do
        [ -f "$desktop" ] || continue
        kind=wayland-sessions
        grep -qx 'X-Glue-SessionType=x11' "$desktop" && kind=xsessions
        ln -s "$desktop" "$session_data/$kind/${desktop##*/}"
    done
    # Preserve fonts, icons, GLib schemas, D-Bus services, and other shared
    # assets while keeping the session directories exclusive to Glue.
    for shared in /usr/share/*; do
        base=${shared##*/}
        case "$base" in xsessions|wayland-sessions) continue ;; esac
        [ -e "$session_data/$base" ] || ln -s "$shared" "$session_data/$base"
    done
    export XDG_DATA_DIRS="$session_data"
    dbus-uuidgen --ensure=/etc/machine-id
    id glue-demo >/dev/null 2>&1 || \
        useradd -m -c 'Glue Demo' -s /bin/bash glue-demo
    install -d -m 755 /var/lib/regreet
    printf '%s\n' 'last_user = "glue-demo"' '' '[user_to_last_sess]' \
        'glue-demo = "gluewc"' > /var/lib/regreet/state.toml
    install -d -m 755 /run/dbus
    rm -f /run/dbus/system_bus_socket
    SYSTEM_DBUS_PID=$(dbus-daemon --system --fork --nopidfile --print-pid=1)
    export SYSTEM_DBUS_PID
    /usr/lib/polkit-1/polkitd --no-debug >/tmp/polkitd.log 2>&1 &
    POLKITD_PID=$!
    export POLKITD_PID
    poll 10 "PolicyKit service" busctl --system --no-pager status org.freedesktop.PolicyKit1
    /usr/lib/accounts-daemon >/tmp/accounts-daemon.log 2>&1 &
    ACCOUNTSD_PID=$!
    export ACCOUNTSD_PID
    poll 10 "AccountsService" busctl --system --no-pager status org.freedesktop.Accounts
    busctl call org.freedesktop.Accounts /org/freedesktop/Accounts \
        org.freedesktop.Accounts CacheUser s glue-demo >/dev/null
    log "system D-Bus pid $SYSTEM_DBUS_PID, PolicyKit pid $POLKITD_PID, AccountsService pid $ACCOUNTSD_PID"
    rm -f /tmp/regreet.log /tmp/regreet-file.log /tmp/regreet.strace "$png"
    trap cleanup_session EXIT

    # Cage supplies WAYLAND_DISPLAY to this child. Set the output mode before
    # GTK creates its first surface, avoiding a partially damaged frame after
    # resizing an already mapped ReGreet window.
    cage -m last -d -- /bin/bash -c '
        wlr-randr --output HEADLESS-1 --custom-mode "${WIDTH}x${HEIGHT}" || exit 70
        exec strace -f -e trace=%file -o /tmp/regreet.strace \
            regreet --demo --verbose --log-level debug \
            --config /etc/greetd/regreet.toml \
            --style /etc/greetd/regreet.css \
            --logs /tmp/regreet-file.log
    ' >/tmp/regreet.log 2>&1 &
    CAGE_PID=$!
    log "Cage pid $CAGE_PID"
    poll "$TIMEOUT" "Cage Wayland socket" wayland_ready
    log "Wayland display $WAYLAND_DISPLAY"
    poll "$TIMEOUT" "ReGreet running as a child of Cage" regreet_under_cage
    kill -0 "$CAGE_PID" || die "Cage exited: $(tail -n 40 /tmp/regreet.log)"

    poll "$TIMEOUT" "all Glue sessions in ReGreet's session list" sessions_loaded
    expected=$(find /usr/share/glue/sessions -maxdepth 1 -name '*.desktop' | wc -l)
    log "ReGreet opened all $expected session entries linked from /usr/share/glue/sessions (strace verified)"

    # Let GTK finish font and layout rendering before capturing its buffer.
    sleep 4
    grim -o HEADLESS-1 "$png" || rc=$?
    [ "$rc" -eq 0 ] || die "grim failed with exit code $rc"
    verify_png "$png"
    log "Cage and ReGreet remained alive through capture"
}

case "${1:-}" in
    --inside) inside_main ;;
    --session) session_main "${2:?output PNG required}" ;;
    -h|--help)
        sed -n '2,7p' "$0"
        ;;
    '') host_main ;;
    *) die "usage: $0 [--help]" ;;
esac
