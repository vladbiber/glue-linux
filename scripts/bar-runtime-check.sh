#!/bin/bash
# bar-runtime-check.sh — probe, headless in the build container, that the
# gluewc bar's runtime really answers (roadmap 5.9), for glueqs and Noctalia.
#
#   sh scripts/bar-runtime-check.sh [glueqs|noctalia|all]
#
# Host side: one `docker run --rm --privileged --network host -v /dev/dri`
# of glue-pkgbuild-img PER SHELL (noctalia-qs CONFLICTS quickshell, so the
# two shells cannot share a container), repo on /glue. Exit code is non-zero
# when any probe fails; a PASS/FAIL table is printed per shell.
#
# Inside (--inside SHELL, root): installs the exact runtime set plan_session.py
# puts on the target, starts a SYSTEM bus with upowerd + power-profiles-daemon
# (no elogind/init needed for the probes), then re-executes under
# dbus-run-session (--session SHELL): gluewc headless -> shell -> probes:
#   upower-ping      org.freedesktop.UPower answers Peer.Ping on the system bus
#   ppd-ping         net.hadess.PowerProfiles answers Peer.Ping
#   ppd-get          `powerprofilesctl get` prints a profile
#   brightnessctl    `brightnessctl -l` runs; a real /sys/class/backlight gets
#                    a `gluewc-backlight up` write too (no backlight in the
#                    container: write test deferred to hardware, see STATE)
#   udev-video-rule  brightnessctl ships a rules.d file granting group video
#                    (the "no sudo" property, checked statically)
#   wl-paste         `wl-paste --list-types` talks to the compositor
#   playerctl        `playerctl --list-all` exits 0
#   gluewc-msg       `gluewc-msg status` answers while the shell runs
#   shell-alive      shell process alive after the start-up window
#   notifications    the shell owns org.freedesktop.Notifications on the
#                    session bus (both shells ship a server, ADR-020)
#   portal-ping      org.freedesktop.portal.Desktop is D-Bus activated
set -u

IMAGE="${GLUE_CHECK_IMAGE:-glue-pkgbuild-img}"
TIMEOUT=60
SHELLS_ALL="glueqs noctalia"
# the target's bar runtime (plan_session.py: _WM_COMMON + _WM_WAYLAND)
RUNTIME="upower power-profiles-daemon brightnessctl playerctl wl-clipboard
         xdg-desktop-portal xdg-desktop-portal-wlr xdg-desktop-portal-gtk libnotify
         dbus elogind pipewire wireplumber polkit"

log() { printf '[bar-check] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# ---------------------------------------------------------------- host ----
host_main() {
    local which=$1 shells s rc=0 root start
    root=$(cd -- "$(dirname -- "$0")/.." && pwd)
    command -v docker >/dev/null 2>&1 || die "docker not found"
    [ -e /dev/dri/renderD128 ] || die "/dev/dri/renderD128 missing: scenefx has no software renderer"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || die "docker image $IMAGE not found (run ./build.sh first)"
    case "$which" in
        all) shells=$SHELLS_ALL ;;
        glueqs|noctalia) shells=$which ;;
        *) die "unknown shell '$which' (glueqs|noctalia|all)" ;;
    esac
    start=$(date +%s)
    for s in $shells; do
        log "=== $s (container $IMAGE)"
        if ! docker run --rm --privileged --network host \
                -v /dev/dri:/dev/dri -v "$root:/glue" -w /glue \
                "$IMAGE" /bin/bash /glue/scripts/bar-runtime-check.sh --inside "$s"; then
            log "$s: FAILED"; rc=1
        fi
    done
    log "done in $(( $(date +%s) - start )) s: $shells (exit $rc)"
    return $rc
}

# ------------------------------------------------------------- container ----
install_local() { # same recipe as screenshots-headless.sh / make-iso.sh
    local name=$1 pkg
    pacman -Q "$name" >/dev/null 2>&1 && { log "$name already installed"; return; }
    pkg=$(ls -1 /glue/packages/"$name"/*.pkg.tar.* /home/builder/pkgs/"$name"-*.pkg.tar.* 2>/dev/null | head -n1 || true)
    if [ -z "$pkg" ]; then
        log "building $name with makepkg"
        rm -rf "/tmp/build-$name"
        cp -r "/glue/packages/$name" "/tmp/build-$name"
        chown -R builder:builder "/tmp/build-$name"
        ( cd "/tmp/build-$name" && sudo -u builder makepkg -f --syncdeps --noconfirm --skippgpcheck )
        pkg=$(ls -1 "/tmp/build-$name"/*.pkg.tar.* | head -n1)
    fi
    pacman -U --noconfirm --needed "$pkg"
}

seed_noctalia() { # no settings => setup wizard; no shell-state => telemetry wizard
    local ver
    ver=$(pacman -Q noctalia-shell | awk '{print $2}' | sed 's/-[0-9]*$//')
    mkdir -p /root/.config/noctalia /root/.cache/noctalia
    printf '{\n  "settingsVersion": 59\n}\n' > /root/.config/noctalia/settings.json
    printf '{\n  "changelogState": { "lastSeenVersion": "%s" }\n}\n' "$ver" \
        > /root/.cache/noctalia/shell-state.json
}

inside_main() {
    local shell=$1 upowerd ppd
    grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
    log "syncing package databases"
    pacman -Sy --noconfirm >/dev/null
    # shellcheck disable=SC2086
    pacman -S --noconfirm --needed $RUNTIME >/dev/null
    install_local gluewc
    case "$shell" in
        glueqs)   install_local glueqs ;;
        noctalia)
            pacman -Q quickshell >/dev/null 2>&1 && pacman -Rdd --noconfirm quickshell >/dev/null
            pacman -S --noconfirm --needed noctalia-qs noctalia-shell >/dev/null ;;
        *) die "unknown shell '$shell'" ;;
    esac
    # the target's portal backend file (plan_greeter._portal_file), same content
    install -Dm644 /dev/stdin /usr/share/xdg-desktop-portal/gluewc-portals.conf <<'CONF'
[preferred]
default=gtk
org.freedesktop.impl.portal.ScreenCast=wlr
org.freedesktop.impl.portal.Screenshot=wlr
CONF
    # system bus + the two system daemons the bar talks to (on the target they
    # are D-Bus activated / enabled as power-profiles-daemon-<init>)
    dbus-uuidgen --ensure
    mkdir -p /run/dbus
    pgrep -x dbus-daemon >/dev/null || dbus-daemon --system --fork
    upowerd=$(pacman -Ql upower | awk '/\/upowerd$/ {print $2}')
    ppd=$(pacman -Ql power-profiles-daemon | awk '/\/power-profiles-daemon$/ && !/share/ {print $2}')
    log "upowerd=$upowerd ppd=$ppd"
    [ -x "$upowerd" ] && [ -x "$ppd" ] || die "daemon binaries not found"
    # upowerd needs the polkit authority; the container's system bus has no
    # launch helper for activation, so polkitd is started by hand first (on
    # the target polkit and upower are both activated by the system bus)
    /usr/lib/polkit-1/polkitd --no-debug >/tmp/polkitd.log 2>&1 &
    sleep 1
    "$upowerd" >/tmp/upowerd.log 2>&1 &
    "$ppd" >/tmp/ppd.log 2>&1 &
    export HOME=/root XDG_RUNTIME_DIR=/tmp/xdg-run
    rm -rf "$XDG_RUNTIME_DIR" /root/.config/gluewc; mkdir -m 700 "$XDG_RUNTIME_DIR"
    [ "$shell" = noctalia ] && seed_noctalia
    dbus-run-session -- /bin/bash /glue/scripts/bar-runtime-check.sh --session "$shell"
}

# ------------------------------------------------- session (under dbus) ----
RESULTS=""
FAILED=0
record() { # record NAME PASS|FAIL "detail"
    RESULTS="$RESULTS$(printf '%-16s %-4s %s' "$1" "$2" "$3")
"
    [ "$2" != FAIL ] || FAILED=$((FAILED + 1))
}
probe() { # probe NAME cmd... — PASS when the command exits 0
    local name=$1 out rc; shift
    out=$("$@" 2>&1); rc=$?
    if [ $rc -eq 0 ]; then record "$name" PASS "$(echo "$out" | head -n1 | cut -c1-60)"
    else record "$name" FAIL "exit $rc: $(echo "$out" | tail -n1 | cut -c1-60)"; fi
    return $rc
}
ping_name() { # ping_name --system|--session NAME PATH
    dbus-send "$1" --print-reply --dest="$2" "$3" org.freedesktop.DBus.Peer.Ping
}
poll() { # poll SECONDS "what" cmd...
    local secs=$1 what=$2 tries=0 max; shift 2; max=$((secs * 5))
    while [ "$tries" -lt "$max" ]; do
        "$@" >/dev/null 2>&1 && return 0
        sleep 0.2; tries=$((tries + 1))
    done
    log "timed out after ${secs}s waiting for $what"; return 1
}
wayland_socket_ready() {
    local s
    for s in "$XDG_RUNTIME_DIR"/wayland-*; do
        case "$s" in *.lock) continue ;; esac
        [ -S "$s" ] || continue
        WAYLAND_DISPLAY="${s##*/}"; export WAYLAND_DISPLAY; return 0
    done
    return 1
}
name_owned() { # name_owned NAME — true when NAME has an owner on the session bus
    dbus-send --session --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus \
        org.freedesktop.DBus.GetNameOwner string:"$1" >/dev/null 2>&1
}
cleanup_session() { set +e; kill $(jobs -p) 2>/dev/null; sleep 1; kill -9 $(jobs -p) 2>/dev/null; return 0; }

session_main() {
    local shell=$1 shell_cmd shell_pid shell_log=/tmp/shell.log t0 out
    t0=$(date +%s)
    export LIBGL_ALWAYS_SOFTWARE=1 GLUE_WELCOME_DISABLED=1
    export XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=gluewc XDG_SESSION_DESKTOP=gluewc
    trap cleanup_session EXIT
    # system daemons first: they do not need the compositor
    poll 20 "org.freedesktop.UPower" ping_name --system org.freedesktop.UPower /org/freedesktop/UPower
    probe upower-ping ping_name --system org.freedesktop.UPower /org/freedesktop/UPower || \
        log "upowerd: $(tail -n 2 /tmp/upowerd.log)"
    poll 20 "net.hadess.PowerProfiles" ping_name --system net.hadess.PowerProfiles /net/hadess/PowerProfiles
    probe ppd-ping ping_name --system net.hadess.PowerProfiles /net/hadess/PowerProfiles
    out=$(powerprofilesctl get 2>&1)
    case "$out" in
        performance|balanced|power-saver) record ppd-get PASS "$out" ;;
        *) record ppd-get FAIL "$(echo "$out" | tail -n1 | cut -c1-60)" ;;
    esac
    if ls /sys/class/backlight/* >/dev/null 2>&1; then
        probe brightnessctl sh -c 'brightnessctl -l && gluewc-backlight up'
    else
        probe brightnessctl brightnessctl -l && \
            record brightnessctl-sys SKIP "no /sys/class/backlight in container: write test on hardware"
    fi
    probe udev-video-rule sh -c 'grep -l "chgrp video" $(pacman -Ql brightnessctl | awk "/rules.d.*rules$/ {print \$2}")'
    probe playerctl playerctl --list-all
    # compositor, headless, then the shell
    mkdir -p /root/.config/gluewc
    cp /usr/share/gluewc/config.def.conf /root/.config/gluewc/config.conf
    printf '\noutput = HEADLESS-1 mode=1920x1080@60\n' >> /root/.config/gluewc/config.conf
    WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128 \
        gluewc >/tmp/gluewc.log 2>&1 &
    poll "$TIMEOUT" "the gluewc wayland socket" wayland_socket_ready || die "gluewc: $(tail -n 5 /tmp/gluewc.log)"
    log "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
    case "$shell" in
        glueqs)   shell_cmd="glueqs" ;;
        noctalia) shell_cmd="qs -c noctalia-shell" ;;
    esac
    $shell_cmd >"$shell_log" 2>&1 &
    shell_pid=$!
    if [ "$shell" = glueqs ]; then
        poll 20 "glueqs 'Configuration Loaded'" grep -q "Configuration Loaded" "$shell_log"
    fi
    poll 20 "org.freedesktop.Notifications owned by the shell" name_owned org.freedesktop.Notifications
    if kill -0 "$shell_pid" 2>/dev/null; then record shell-alive PASS "$shell_cmd pid $shell_pid"
    else record shell-alive FAIL "$(tail -n 1 "$shell_log" | cut -c1-60)"; fi
    probe notifications name_owned org.freedesktop.Notifications
    probe gluewc-msg gluewc-msg status
    # wl-paste: an EMPTY selection is fine ("No selection"), a missing display is not
    out=$(wl-paste --list-types 2>&1); rc=$?
    if [ $rc -eq 0 ] || echo "$out" | grep -qi "no selection\|nothing is copied"; then record wl-paste PASS "$(echo "$out" | head -n1 | cut -c1-60)"
    else record wl-paste FAIL "exit $rc: $(echo "$out" | tail -n1 | cut -c1-60)"; fi
    probe portal-ping ping_name --session org.freedesktop.portal.Desktop /org/freedesktop/portal/desktop
    printf '\n== %s: bar runtime probes (%s s) ==\n%s' "$shell" "$(( $(date +%s) - t0 ))" "$RESULTS"
    tail -n 3 "$shell_log" >&2 || true
    [ "$FAILED" -eq 0 ] || { log "$FAILED probe(s) failed for $shell"; return 1; }
    log "all probes passed for $shell"
}

case "${1:-all}" in
    --inside)  inside_main "${2:?shell}" ;;
    --session) session_main "${2:?shell}" ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *)         host_main "${1:-all}" ;;
esac
