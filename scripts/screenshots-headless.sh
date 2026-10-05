#!/bin/bash
# screenshots-headless.sh - real 1920x1080 catalog screenshots of the Glue
# sessions, taken headless inside the build container.
#
#   sh scripts/screenshots-headless.sh [glueqs|noctalia|nvwm|all]
#
# Host side: one `docker run --rm --privileged -v /dev/dri:/dev/dri` of
# glue-pkgbuild-img PER TARGET, with the repo bind-mounted on /glue. One
# container per target because glueqs needs the `quickshell` package while
# noctalia-shell needs `noctalia-qs`, which CONFLICTS with (and PROVIDES)
# quickshell: the two providers cannot be installed side by side, so each
# capture gets its own throwaway container. --network host: the docker bridge
# has no IPv4 forwarding on the build host, host networking is what works.
#
# Inside (--inside TARGET, runs as root in the container): installs what the
# capture needs (local packages + repos), then re-executes itself under
# dbus-run-session (--session TARGET) exactly like the installed session
# wrappers do (plan_greeter.py): compositor -> poll the wayland socket ->
# shell -> clients -> grim. nvwm is X11: Xvfb :9 + nvwm + `import -window root`.
#
# Content of every shot: alacritty running fastfetch (Glue logo from
# /etc/glue/fastfetch.jsonc, glue-branding) and a firefox window
# on a local HTML page (no network), tiled side by side by the window manager.
# The gluewc shells run on the packaged Stillwater wallpaper (set through the
# shell's own IPC, like glue-wallpaper-init); nvwm gets it through feh.
#
# gluewc targets produce TWO captures in one container run: `-bar` (desktop +
# bar) and `-overview`. The compositor has no IPC for the overview (it is
# toggled by a tap on Super, gluewc.c keypress), so `vkbd` - gluewc's own
# virtual-keyboard test injector, built here from the gluewc sources - sends a
# real Super tap. Both pictures are plain grim captures of the live output.
#
# Output: packages/glue-installer/catalog/screenshots/
#   gluewc-glueqs-{bar,overview}.png  gluewc-noctalia-{bar,overview}.png  nvwm.png
# Each is verified to be a 1920x1080 PNG with > 1000 unique colours.
set -eu

WIDTH=1920
HEIGHT=1080
MIN_COLORS=1000
MAX_BYTES=$((3 * 1024 * 1024))
TIMEOUT=60                       # seconds to wait for sockets / windows
IMAGE="${GLUE_SCREENSHOT_IMAGE:-glue-pkgbuild-img}"
TARGETS_ALL="glueqs noctalia nvwm"

log() { printf '[screenshots] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

WALLPAPER=/usr/share/backgrounds/glue/wallpaper.png   # Stillwater, glue-branding

# every file a target produces (first = the "bar" picture, shown first in the TUI)
files_for() {
    case "$1" in
        glueqs)   echo gluewc-glueqs-bar.png gluewc-glueqs-overview.png ;;
        noctalia) echo gluewc-noctalia-bar.png gluewc-noctalia-overview.png ;;
        nvwm)     echo nvwm.png ;;
        *) die "unknown target '$1' (glueqs|noctalia|nvwm|all)" ;;
    esac
}

# magick identify: fail unless WxH PNG with enough unique colours
verify_png() {
    local png=$1 w h k
    [ -s "$png" ] || die "$png missing or empty"
    set -- $(magick identify -format '%w %h %k\n' "$png" | head -n1)
    w=${1:-0}; h=${2:-0}; k=${3:-0}
    log "$png: ${w}x${h}, $k unique colours, $(stat -c %s "$png") bytes"
    [ "$w" = "$WIDTH" ] && [ "$h" = "$HEIGHT" ] || die "$png is ${w}x${h}, want ${WIDTH}x${HEIGHT}"
    [ "$k" -gt "$MIN_COLORS" ] || die "$png has only $k unique colours (blank capture?)"
}

# ---------------------------------------------------------------- host ----
host_main() {
    local target="$1" targets t root out start
    root=$(cd -- "$(dirname -- "$0")/.." && pwd)
    out="$root/packages/glue-installer/catalog/screenshots"
    command -v docker >/dev/null 2>&1 || die "docker not found"
    command -v magick >/dev/null 2>&1 || die "imagemagick (magick) not found on the host"
    [ -e /dev/dri/renderD128 ] || die "/dev/dri/renderD128 missing: scenefx has no software renderer"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || die "docker image $IMAGE not found (run ./build.sh first)"
    mkdir -p "$out"
    case "$target" in
        all) targets=$TARGETS_ALL ;;
        *) files_for "$target" >/dev/null; targets=$target ;;
    esac
    start=$(date +%s)
    for t in $targets; do
        log "=== $t -> $(files_for "$t") (container $IMAGE)"
        docker run --rm --privileged --network host \
            -v /dev/dri:/dev/dri -v "$root:/glue" -w /glue \
            -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
            "$IMAGE" /bin/bash /glue/scripts/screenshots-headless.sh --inside "$t"
        for f in $(files_for "$t"); do verify_png "$out/$f"; done
    done
    for t in $targets; do
        for f in $(files_for "$t"); do
            local png="$out/$f"
            if [ "$(stat -c %s "$png")" -gt "$MAX_BYTES" ]; then
                log "recompressing $png (> 3 MB)"
                magick "$png" -define png:compression-level=9 "$png.tmp" && mv "$png.tmp" "$png"
                verify_png "$png"
            fi
        done
    done
    log "done in $(( $(date +%s) - start )) s: $targets"
}

# ------------------------------------------------------------- container ----
# Install a locally built package (packages/NAME/*.pkg.tar.zst, or the build
# container's /home/builder/pkgs), building it with makepkg as `builder` when
# no package file exists - the same recipe as make-iso.sh.
install_local() {
    local name=$1 pkg
    pacman -Q "$name" >/dev/null 2>&1 && { log "$name already installed"; return; }
    if [ -n "${GLUE_FROM_TREE:-}" ]; then
        pkg=
    else
    # what the ISO ships wins: the [glue] repo of the last build
    pkg=$(ls -1 /glue/.glue-repo/"$name"-[0-9r]*.pkg.tar.zst 2>/dev/null | tail -n1 || true)
    [ -n "$pkg" ] || pkg=$(ls -1 /glue/packages/"$name"/*.pkg.tar.* /home/builder/pkgs/"$name"-*.pkg.tar.* 2>/dev/null | head -n1 || true)
    fi
    if [ -z "$pkg" ]; then
        log "building $name with makepkg"
        rm -rf "/tmp/build-$name"
        cp -r "/glue/packages/$name" "/tmp/build-$name"
        chown -R builder:builder "/tmp/build-$name"
        ( cd "/tmp/build-$name" && \
          sudo -u builder makepkg -f --syncdeps --noconfirm --skippgpcheck )
        pkg=$(ls -1 "/tmp/build-$name"/*.pkg.tar.* | head -n1)
    fi
    log "installing $pkg"
    pacman -U --noconfirm --needed "$pkg"
}

inside_main() {
    local target=$1 outdir f
    outdir=/glue/packages/glue-installer/catalog/screenshots
    [ -w /glue ] || die "/glue is not writable"
    grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
    # the other mirrors lag behind mirror1: a database from one and packages
    # from another 404 (older images do not list mirror1 first)
    grep -q '^Server = https://mirror1.artixlinux.org' /etc/pacman.d/mirrorlist ||
        sed -i '1i Server = https://mirror1.artixlinux.org/repos/$repo/os/$arch' /etc/pacman.d/mirrorlist
    log "syncing package databases"
    pacman -Sy --noconfirm
    pacman -S --noconfirm --needed alacritty fastfetch firefox imagemagick \
        dbus ttf-dejavu ttf-liberation
    # glue-branding from the tree (fastfetch config and wallpaper as committed)
    GLUE_FROM_TREE=1 install_local glue-branding
    # on a real system /etc/os-release is a symlink to /usr/lib/os-release (the
    # file glue-branding rewrites); the image has a plain Artix copy, so fastfetch
    # would print the Artix name in the shot
    ln -sf ../usr/lib/os-release /etc/os-release
    echo glue-live > /proc/sys/kernel/hostname 2>/dev/null || true   # the live hostname, not the build host's
    case "$target" in
        glueqs)
            install_local gluewc
            install_local glueqs
            pacman -S --noconfirm --needed grim
            build_vkbd
            ;;
        noctalia)
            install_local gluewc
            # noctalia-qs PROVIDES+CONFLICTS quickshell: drop the galaxy
            # provider (and glueqs, which depends on it) in THIS container only
            if pacman -Q quickshell >/dev/null 2>&1; then
                pacman -Rdd --noconfirm quickshell
            fi
            pacman -S --noconfirm --needed grim noctalia-qs noctalia-shell
            build_vkbd
            ;;
        nvwm)
            install_local nvwm
            pacman -S --noconfirm --needed xorg-server-xvfb xorg-xwininfo feh
            ;;
    esac
    export HOME=/root
    export XDG_RUNTIME_DIR=/tmp/xdg-run
    rm -rf "$XDG_RUNTIME_DIR"; mkdir -m 700 "$XDG_RUNTIME_DIR"
    rm -rf /root/.config/gluewc /root/.local/state/gluewc /tmp/ff
    mkdir -p /root/.config/fastfetch
    cp /etc/glue/fastfetch.jsonc /root/.config/fastfetch/config.jsonc
    write_firefox_profile
    [ "$target" = noctalia ] && seed_noctalia
    for f in $(files_for "$target"); do rm -f "$outdir/$f"; done
    # the installed wrappers run the session under dbus-run-session; so do we
    dbus-run-session -- /bin/bash /glue/scripts/screenshots-headless.sh --session "$target" "$outdir"
    for f in $(files_for "$target"); do
        verify_png "$outdir/$f"
        chown "${HOST_UID:-0}:${HOST_GID:-0}" "$outdir/$f"
    done
}

# vkbd (virtual-keyboard injector) is a test helper of the gluewc repo that the
# gluewc package does not ship and the packaged source checkout does not carry:
# fetch it from the commit the gluewc package is pinned to and build it here.
GLUEWC_RAW=https://raw.githubusercontent.com/vladbiber/gluewc/94acc30/tests
build_vkbd() {
    local d=/tmp/vkbd-build f
    rm -r -f "$d"; mkdir -p "$d"
    for f in vkbd.c virtual-keyboard-unstable-v1.xml; do
        curl -fsSL "$GLUEWC_RAW/$f" -o "$d/$f" || die "cannot fetch $GLUEWC_RAW/$f"
    done
    ( cd "$d" &&
      wayland-scanner client-header virtual-keyboard-unstable-v1.xml vkbd-protocol.h &&
      wayland-scanner private-code virtual-keyboard-unstable-v1.xml vkbd-protocol.c &&
      gcc -D_GNU_SOURCE $(pkg-config --cflags wayland-client xkbcommon) -o vkbd \
          vkbd.c vkbd-protocol.c $(pkg-config --libs wayland-client xkbcommon) ) >&2 \
        || die "building vkbd failed"
    install -m755 "$d/vkbd" /usr/local/bin/vkbd
}

# Started without settings.json Noctalia opens its setup wizard over the whole
# desktop, and with settings but no shell-state it opens the telemetry wizard
# (Commons/Settings.qml, Services/Noctalia/UpdateService.qml). Seed both with
# the installed version so the capture shows the bar, not a wizard.
seed_noctalia() {
    local ver
    ver=$(pacman -Q noctalia-shell | awk '{print $2}' | sed 's/-[0-9]*$//')
    mkdir -p /root/.config/noctalia /root/.cache/noctalia
    printf '{\n  "settingsVersion": 59\n}\n' > /root/.config/noctalia/settings.json
    printf '{\n  "changelogState": { "lastSeenVersion": "%s" }\n}\n' "$ver" \
        > /root/.cache/noctalia/shell-state.json
    log "seeded noctalia settings (version $ver)"
}

write_firefox_profile() {
    mkdir -p /tmp/ff
    # fresh profile without first-run tabs (they need the network)
    cat > /tmp/ff/user.js <<'EOF'
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("datareporting.policy.firstRunURL", "");
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

# ------------------------------------------------- session (under dbus) ----
poll() { # poll SECONDS "description" cmd args... - polls at 0.2 s like the wrappers
    local secs=$1 what=$2; shift 2
    local tries=0 max=$((secs * 5))
    while [ "$tries" -lt "$max" ]; do
        if "$@" >/dev/null 2>&1; then return 0; fi
        sleep 0.2; tries=$((tries + 1))
    done
    die "timed out after ${secs}s waiting for $what"
}

wayland_socket_ready() {
    local s
    for s in "$XDG_RUNTIME_DIR"/wayland-*; do
        case "$s" in *.lock) continue ;; esac
        [ -S "$s" ] || continue
        WAYLAND_DISPLAY="${s##*/}"; export WAYLAND_DISPLAY
        return 0
    done
    return 1
}

gluewc_clients_ready() { # at least 2 clients on the output (alacritty + firefox)
    local line total=0 n
    line=$(gluewc-msg status 2>/dev/null | head -n1) || return 1
    [ -n "$line" ] || return 1
    for n in $(echo "$line" | awk '{print $4}' | tr ',' ' '); do
        total=$((total + n))
    done
    [ "$total" -ge 2 ]
}

x11_clients_ready() {
    [ "$(xwininfo -root -tree 2>/dev/null | grep -ci 'alacritty\|firefox')" -ge 2 ]
}

launch_clients() { # $1 = x11|wayland
    alacritty -e bash -c 'fastfetch; exec bash --norc' &
    log "alacritty pid $!"
    if [ "$1" = wayland ]; then
        MOZ_ENABLE_WAYLAND=1 \
            firefox --no-remote --new-instance --profile /tmp/ff \
                    --new-window file:///tmp/ff/glue.html &
    else
        MOZ_ENABLE_WAYLAND=0 \
            firefox --no-remote --new-instance --profile /tmp/ff \
                    --new-window file:///tmp/ff/glue.html &
    fi
    log "firefox pid $!"
}

# only our own children: the surrounding dbus-run-session must outlive us
cleanup_session() {
    set +e
    kill $(jobs -p) 2>/dev/null
    sleep 1
    kill -9 $(jobs -p) 2>/dev/null
    return 0
}

session_main() {
    local target=$1 outdir=$2 png shell_cmd shell_log
    png="$outdir/$(files_for "$target" | awk '{print $1}')"
    export LIBGL_ALWAYS_SOFTWARE=1 GLUE_WELCOME_DISABLED=1
    trap cleanup_session EXIT
    log "D-Bus: ${DBUS_SESSION_BUS_ADDRESS:-none}"
    if [ "$target" = nvwm ]; then
        rm -f /tmp/.X9-lock /tmp/.X11-unix/X9
        Xvfb :9 -screen 0 "${WIDTH}x${HEIGHT}x24" -nolisten tcp >/tmp/xvfb.log 2>&1 &
        log "Xvfb pid $!"
        export DISPLAY=:9
        poll "$TIMEOUT" "Xvfb :9" test -S /tmp/.X11-unix/X9
        export XDG_SESSION_TYPE=x11 XDG_CURRENT_DESKTOP=nvwm
        nvwm >/tmp/nvwm.log 2>&1 &
        NVWM_PID=$!
        log "nvwm pid $NVWM_PID"
        sleep 2
        kill -0 "$NVWM_PID" || die "nvwm exited: $(cat /tmp/nvwm.log)"
        feh --no-fehbg --bg-fill "$WALLPAPER" || log "feh failed"
        launch_clients x11
        poll "$TIMEOUT" "alacritty + firefox under nvwm" x11_clients_ready
        sleep 8   # fastfetch + firefox paint
        log "import -window root -> $png (nvwm pid $NVWM_PID alive: $(kill -0 "$NVWM_PID" && echo yes || echo no))"
        rc=0
        import -display :9 -window root "$png" >/tmp/import.log 2>&1 || rc=$?
        log "import exit: $rc, output: $(cat /tmp/import.log)"
        [ "$rc" -eq 0 ] || die "import failed"
        return 0
    fi
    # gluewc, headless wlroots backend, one 1920x1080 output
    mkdir -p /root/.config/gluewc
    cp /usr/share/gluewc/config.def.conf /root/.config/gluewc/config.conf
    sed -i 's/^root_color *= *.*/root_color     = E9E9E7/' /root/.config/gluewc/config.conf
    printf '\noutput = HEADLESS-1 mode=%sx%s@60\n' "$WIDTH" "$HEIGHT" >> /root/.config/gluewc/config.conf
    export XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=gluewc XDG_SESSION_DESKTOP=gluewc
    WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 \
        WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128 gluewc >/tmp/gluewc.log 2>&1 &
    log "gluewc pid $!"
    poll "$TIMEOUT" "the gluewc wayland socket" wayland_socket_ready
    log "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
    case "$target" in
        glueqs)   shell_cmd="glueqs" ;;
        noctalia) shell_cmd="qs -c noctalia-shell" ;;
    esac
    shell_log=/tmp/shell.log
    $shell_cmd >"$shell_log" 2>&1 &
    SHELL_PID=$!
    log "shell ($shell_cmd) pid $SHELL_PID"
    if [ "$target" = glueqs ]; then
        poll "$TIMEOUT" "glueqs 'Configuration Loaded'" grep -q "Configuration Loaded" "$shell_log"
    else
        poll "$TIMEOUT" "noctalia-shell to start" sh -c "[ -s $shell_log ] && kill -0 $SHELL_PID"
    fi
    kill -0 "$SHELL_PID" || die "shell exited: $(tail -n 20 "$shell_log")"
    sleep 8   # let the bar settle before windows open
    # Stillwater through the shell's own IPC (what glue-wallpaper-init does)
    case "$target" in
        glueqs)   poll "$TIMEOUT" "glueqs setwallpaper" qs -c glueqs ipc call glueqs setwallpaper "$WALLPAPER" ;;
        noctalia) poll "$TIMEOUT" "noctalia wallpaper set" qs -c noctalia-shell ipc call wallpaper set "$WALLPAPER" all ;;
    esac
    sleep 4   # wallpaper decode + fade
    launch_clients wayland
    poll "$TIMEOUT" "alacritty + firefox under gluewc" gluewc_clients_ready
    sleep 8   # fastfetch output + firefox first paint
    log "gluewc-msg status: $(gluewc-msg status 2>&1 | head -n1)"
    grim -o HEADLESS-1 "$png" || die "grim failed (bar capture)"
    log "bar capture: $png"
    # overview: a real tap on Super, exactly what the keypress handler waits for
    vkbd Super || die "vkbd could not tap Super"
    sleep 5   # overview animation
    grim -o HEADLESS-1 "$outdir/$(files_for "$target" | awk '{print $2}')" \
        || die "grim failed (overview capture)"
    tail -n 5 "$shell_log" >&2 || true
}

case "${1:-all}" in
    --inside)  inside_main "${2:?target}" ;;
    --session) session_main "${2:?target}" "${3:?png}" ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *)         host_main "${1:-all}" ;;
esac
