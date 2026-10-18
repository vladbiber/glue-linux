#!/bin/bash
# screenshots-headless.sh - real 1920x1080 catalog screenshots of the Glue
# sessions, taken headless inside the build container.
#
#   sh scripts/screenshots-headless.sh [glueqs|noctalia|caelestia|nvwm|all]
#
# Host side: one `docker run --rm --privileged -v /dev/dri:/dev/dri` of
# glue-build (GLUE_SCREENSHOT_IMAGE, entrypoint overridden) PER TARGET, with
# the repo bind-mounted on /glue. Each capture gets its own throwaway
# container. --network host: the docker bridge has no IPv4 forwarding on the
# build host, host networking is what works.
#
# Noctalia is v5 (native C++, no Qt), built from the pinned upstream release
# with meson: [cachyos] noctalia-shell 5.2.1-1 only ships the v5 source tree,
# and the last QML release (v4.7.7) breaks on Qt 6.12. The built tree is cached
# in out/screenshot-cache/, so only the first noctalia run compiles (~10 min).
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
# gluewc runs nested in a headless cage: on the headless backend alone gluewc
# never advertises pointer/keyboard on its seat, so injected clicks and typing
# would never reach the shell. Through cage's window gluewc gets a real seat.
# `vkbd`/`vptr` - gluewc's virtual-keyboard/-pointer test injectors, built here
# from the gluewc sources - drive cage, grim captures cage's 1920x1080 output.
#
# gluewc targets produce `-bar` (desktop + bar), `-overview` (a real Super tap,
# the compositor has no IPC for it) and then the shell's own menus and panels,
# opened by clicks on the bar or the shell's IPC (glueqs_extras).
#
# Caelestia is the packaged caelestia-shell built from the tree (its gluewc
# patch, packages/caelestia-shell/glue, is what the shots must show).
#
# Output: packages/glue-installer/catalog/screenshots/
#   gluewc-glueqs-*.png  gluewc-noctalia-*.png  gluewc-caelestia-*.png  nvwm.png
# Each is verified to be a 1920x1080 PNG with > 1000 unique colours.
set -eu

WIDTH=1920
HEIGHT=1080
MIN_COLORS=1000
MAX_BYTES=$((3 * 1024 * 1024))
TIMEOUT=60                       # seconds to wait for sockets / windows
IMAGE="${GLUE_SCREENSHOT_IMAGE:-glue-build}"
TARGETS_ALL="glueqs noctalia caelestia nvwm"

log() { printf '[screenshots] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

WALLPAPER=/usr/share/backgrounds/glue/wallpaper.png   # Stillwater, glue-branding

# every file a target produces (first = the "bar" picture, shown first in the TUI)
files_for() {
    case "$1" in
        glueqs)   echo gluewc-glueqs-bar.png gluewc-glueqs-overview.png \
                       gluewc-glueqs-launcher.png gluewc-glueqs-calendar.png \
                       gluewc-glueqs-notifications.png gluewc-glueqs-settings.png \
                       gluewc-glueqs-wallpapers.png gluewc-glueqs-colors.png \
                       gluewc-glueqs-colors-bar.png gluewc-glueqs-font.png \
                       gluewc-glueqs-fonts.png ;;
        noctalia) echo gluewc-noctalia-bar.png gluewc-noctalia-overview.png \
                       gluewc-noctalia-launcher.png gluewc-noctalia-control-center.png \
                       gluewc-noctalia-notifications.png gluewc-noctalia-settings.png \
                       gluewc-noctalia-wallpapers.png ;;
        caelestia) echo gluewc-caelestia-bar.png gluewc-caelestia-overview.png \
                       gluewc-caelestia-launcher.png gluewc-caelestia-dashboard.png \
                       gluewc-caelestia-sidebar.png gluewc-caelestia-session.png ;;
        nvwm)     echo nvwm.png ;;
        *) die "unknown target '$1' (glueqs|noctalia|caelestia|nvwm|all)" ;;
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
        # glue-build's entrypoint is make-iso.sh: override it or a full ISO build starts
        docker run --rm --privileged --network host --entrypoint /bin/bash \
            -v /dev/dri:/dev/dri -v "$root:/glue" -w /glue \
            -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
            "$IMAGE" /glue/scripts/screenshots-headless.sh --inside "$t"
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
    case "$pkg" in /glue/.glue-repo/*) glue_keyring ;; esac
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

# the [glue] packages carry signatures from a key the image does not know:
# install glue-keyring itself without its .sig (LocalFileSigLevel = Optional)
glue_keyring() {
    [ -e /usr/share/pacman/keyrings/glue.gpg ] && return
    local kr
    kr=$(ls -1 /glue/.glue-repo/glue-keyring-*.pkg.tar.zst | tail -n1) || die "no glue-keyring in .glue-repo"
    cp "$kr" /tmp/
    pacman -U --noconfirm "/tmp/${kr##*/}"
    pacman-key --populate glue
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
            install_local cage
            pacman -S --noconfirm --needed grim wlr-randr libnotify
            # the curated families of the LOOK page's font grid
            pacman -S --noconfirm --needed ttf-jetbrains-mono \
                ttf-cascadia-code-nerd noto-fonts gsfonts
            build_injectors
            ;;
        noctalia)
            install_local gluewc
            install_local cage
            pacman -S --noconfirm --needed grim wlr-randr libnotify
            install_noctalia
            build_injectors
            ;;
        caelestia)
            install_local gluewc
            install_local cage
            pacman -S --noconfirm --needed grim wlr-randr libnotify
            for f in swappy libcava qt6-m3shapes python-materialyoucolor caelestia-cli \
                     ttf-material-symbols-variable ttf-rubik-vf; do install_local "$f"; done
            GLUE_FROM_TREE=1 install_local caelestia-shell
            build_injectors
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
    # gluewc creates only the last level of its state dir (the workspaces file
    # the bar's workspace widget and the overview dock read)
    mkdir -p /root/.local/state
    mkdir -p /root/.config/fastfetch
    cp /etc/glue/fastfetch.jsonc /root/.config/fastfetch/config.jsonc
    write_firefox_profile
    [ "$target" = noctalia ] && seed_noctalia
    case "$target" in glueqs|noctalia) seed_wallpapers ;; esac
    for f in $(files_for "$target"); do rm -f "$outdir/$f"; done
    # the installed wrappers run the session under dbus-run-session; so do we
    dbus-run-session -- /bin/bash /glue/scripts/screenshots-headless.sh --session "$target" "$outdir"
    for f in $(files_for "$target"); do
        verify_png "$outdir/$f"
        chown "${HOST_UID:-0}:${HOST_GID:-0}" "$outdir/$f"
    done
}

# vkbd/vptr (virtual-keyboard/-pointer injectors) are test helpers of the
# gluewc repo that the gluewc package does not ship and the packaged source
# checkout does not carry: fetch them from the commit the gluewc package is
# pinned to and build them here. Two local fixes: vkbd gets the letters its
# table lacks (a d e p u x y z, space, BackSpace) and vptr's `release` no longer
# parses as a right-button `rrelease` (it left the left button held down).
GLUEWC_RAW=https://raw.githubusercontent.com/vladbiber/gluewc/94acc30/tests
build_injectors() {
    local d=/tmp/injectors-build f
    rm -r -f "$d"; mkdir -p "$d"
    for f in vkbd.c virtual-keyboard-unstable-v1.xml vptr.c wlr-virtual-pointer-unstable-v1.xml; do
        curl -fsSL "$GLUEWC_RAW/$f" -o "$d/$f" || die "cannot fetch $GLUEWC_RAW/$f"
    done
    sed -i 's/{"Print",99},/&\n\t{"a",30},{"d",32},{"e",18},{"p",25},{"u",22},{"x",45},{"y",21},{"z",44},{"space",57},{"BackSpace",14},/' "$d/vkbd.c"
    sed -i "s/int right = argv\[i\]\[0\] == 'r';/int right = argv[i][0] == 'r' \&\& (argv[i][1] == 'p' || argv[i][1] == 'r');/" "$d/vptr.c"
    grep -q '"BackSpace",14' "$d/vkbd.c" || die "vkbd key table patch did not apply"
    grep -q "argv\[i\]\[1\] == 'p'" "$d/vptr.c" || die "vptr release patch did not apply"
    ( cd "$d" &&
      wayland-scanner client-header virtual-keyboard-unstable-v1.xml vkbd-protocol.h &&
      wayland-scanner private-code virtual-keyboard-unstable-v1.xml vkbd-protocol.c &&
      gcc -D_GNU_SOURCE $(pkg-config --cflags wayland-client xkbcommon) -o vkbd \
          vkbd.c vkbd-protocol.c $(pkg-config --libs wayland-client xkbcommon) &&
      wayland-scanner client-header wlr-virtual-pointer-unstable-v1.xml vptr-protocol.h &&
      wayland-scanner private-code wlr-virtual-pointer-unstable-v1.xml vptr-protocol.c &&
      gcc -D_GNU_SOURCE $(pkg-config --cflags wayland-client) -o vptr \
          vptr.c vptr-protocol.c $(pkg-config --libs wayland-client) -lm ) >&2 \
        || die "building vkbd/vptr failed"
    install -m755 "$d/vkbd" "$d/vptr" /usr/local/bin/
}

# The glueqs wallpaper picker lists ~/Pictures/Wallpapers: the two Glue
# wallpapers (CC0) and four colour treatments of Stillwater (CC0 derivatives).
WALLDIR=/root/Pictures/Wallpapers
seed_wallpapers() {
    local s=/usr/share/backgrounds/glue
    rm -r -f "$WALLDIR"; mkdir -p "$WALLDIR"
    cp "$s/wallpaper.png" "$WALLDIR/stillwater.png"
    cp "$s/glue-mac.jpg" "$WALLDIR/glue-mac.jpg"
    magick "$s/wallpaper.png" -sepia-tone 80% "$WALLDIR/stillwater-sepia.jpg"
    magick "$s/wallpaper.png" -colorspace gray -fill '#2b4c7e' -tint 100 "$WALLDIR/stillwater-blue.jpg"
    magick "$s/wallpaper.png" -colorspace gray +level-colors '#1d0b0b,#e9a46b' "$WALLDIR/stillwater-dusk.jpg"
    magick "$s/wallpaper.png" -negate -colorspace gray "$WALLDIR/stillwater-night.jpg"
}

# Noctalia v5 from the upstream release tarball (same flags as upstream's
# `just configure release`, tests off). Runtime and build deps are BUILDING.md's
# Arch list; noctalia-qs/quickshell are not needed.
NOCTALIA_VER=5.2.1
NOCTALIA_URL=https://github.com/noctalia-dev/noctalia-shell/releases/download/v$NOCTALIA_VER/noctalia-v$NOCTALIA_VER.tar.gz
NOCTALIA_SHA256=fddff2d78677b8889709c8b2e707896fce1b79d24cb9a81d36f3be6aa66e58bb
NOCTALIA_CACHE=/glue/out/screenshot-cache/noctalia-$NOCTALIA_VER-x86_64.tar
install_noctalia() {
    local d=/tmp/noctalia-build
    pacman -S --noconfirm --needed meson ninja gcc \
        wayland wayland-protocols libglvnd freetype2 fontconfig \
        cairo pango harfbuzz libxkbcommon glib2 libsecret libsodium \
        sdbus-cpp libpipewire wireplumber polkit pam curl libwebp libjxl \
        libsndfile librsvg libqalculate libxml2 md4c tomlplusplus libical \
        nlohmann-json stb jemalloc
    if [ ! -s "$NOCTALIA_CACHE" ]; then
        log "building noctalia $NOCTALIA_VER with meson (cached afterwards)"
        rm -r -f "$d"; mkdir -p "$d/src" "$d/dest"
        curl -fsSL "$NOCTALIA_URL" -o "$d/src.tar.gz" || die "cannot fetch $NOCTALIA_URL"
        echo "$NOCTALIA_SHA256  $d/src.tar.gz" | sha256sum -c - >&2 || die "noctalia tarball checksum mismatch"
        tar xzf "$d/src.tar.gz" -C "$d/src" --strip-components=1
        ( cd "$d/src" &&
          meson setup build --prefix=/usr --buildtype=release -Db_lto=true -Dtests=disabled &&
          meson compile -C build &&
          meson install -C build --destdir "$d/dest" ) >&2 || die "building noctalia failed"
        mkdir -p "${NOCTALIA_CACHE%/*}"
        tar cf "$NOCTALIA_CACHE.tmp" -C "$d/dest" . && mv "$NOCTALIA_CACHE.tmp" "$NOCTALIA_CACHE"
        chown -R "${HOST_UID:-0}:${HOST_GID:-0}" "${NOCTALIA_CACHE%/*}"
    fi
    tar xf "$NOCTALIA_CACHE" -C / --no-same-owner --no-overwrite-dir
    command -v noctalia >/dev/null || die "noctalia not installed"
}

# No setup wizard (it opens over the whole desktop until a marker exists), no
# telemetry ping; the wallpaper picker browses the seeded folder.
seed_noctalia() {
    rm -r -f /root/.config/noctalia /root/.local/state/noctalia
    mkdir -p /root/.config/noctalia
    cat > /root/.config/noctalia/config.toml <<EOF
[shell]
setup_wizard_enabled = false
telemetry_enabled = false

[wallpaper]
directory = "$WALLDIR"
EOF
    log "seeded noctalia config"
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

wayland_socket_ready() { # [taken socket name]: the first other one wins
    local s
    for s in "$XDG_RUNTIME_DIR"/wayland-*; do
        case "$s" in *.lock) continue ;; esac
        [ -S "$s" ] || continue
        [ "${s##*/}" = "${1:-}" ] && continue
        WAYLAND_DISPLAY="${s##*/}"; export WAYLAND_DISPLAY
        return 0
    done
    return 1
}

# input and captures go to cage (CAGE_DISPLAY), everything else to gluewc
snap() { # snap NAME -> gluewc-TARGET-NAME.png
    local f="$OUTDIR/gluewc-$TARGET-$1.png"
    WAYLAND_DISPLAY=$CAGE_DISPLAY grim -o HEADLESS-1 "$f" || die "grim failed ($1)"
    log "capture: $f"
}
key() { WAYLAND_DISPLAY=$CAGE_DISPLAY vkbd "$@" || die "vkbd $* failed"; }
click() { # click X Y (pixels): glide there, settle, left click
    local x y
    x=$(awk -v v="$1" -v w="$WIDTH" 'BEGIN { printf "%.4f", v / w }')
    y=$(awk -v v="$2" -v h="$HEIGHT" 'BEGIN { printf "%.4f", v / h }')
    WAYLAND_DISPLAY=$CAGE_DISPLAY vptr line 0.5 0.6 "$x" "$y" 8 sleep 200 \
        press sleep 80 release || die "vptr click $1 $2 failed"
}
park_pointer() { # off the bar, over the wallpaper strip between the windows
    WAYLAND_DISPLAY=$CAGE_DISPLAY vptr abs 0.5 0.98 >/dev/null 2>&1 || true
}

# Bar positions are those of glueqs' default layout at 1920x1080
# (Settings.qml barLeft/barCenter/barRight; the centre group includes the
# weather tile, which needs the network the container has anyway).
glueqs_extras() {
    local ipc="qs -c glueqs ipc call glueqs"
    # launcher: the dot-grid tile, then a query typed on the keyboard
    click 76 22
    sleep 1
    key f i r e
    sleep 2; snap launcher
    key Escape; sleep 1
    # calendar: the clock tile
    click 1000 22
    sleep 2; snap calendar
    click 1000 22; sleep 1
    # notifications: three real ones through the shell's notification server
    notify-send -a "Glue Apps" "Updates installed" "12 packages were upgraded."
    notify-send -a "Firefox" "Download finished" "glue-linux-x86_64.iso"
    notify-send -a "Glue Welcome" "Welcome to Glue Linux" "Light. Fast. Yours."
    sleep 7   # toasts time out
    click 1116 22
    sleep 2; snap notifications
    click 1116 22; sleep 1
    park_pointer
    # settings, opened on the THEME page
    $ipc settingspage 2
    sleep 3; snap settings
    $ipc close; sleep 2
    # wallpaper picker over ~/Pictures/Wallpapers
    $ipc wallpaper
    sleep 4; snap wallpapers
    $ipc close; sleep 1
    # another scheme: the bar alone, then the THEME page listing the schemes
    glueqs_set themeScheme '"amber"'
    sleep 4; snap colors-bar
    $ipc settingspage 2
    sleep 3; snap colors
    $ipc close; sleep 2
    # plain text in a serif instead of the dot font, then the LOOK page's font grid
    glueqs_set themeScheme '"nothing"' dotFont false uiFont '"Noto Serif"'
    sleep 4; snap font
    $ipc settingspage 3
    sleep 2
    $ipc settingsscroll 320
    sleep 2; snap fonts
    $ipc close; sleep 1
}

# glueqs_set KEY JSON...: edit settings.json, the shell reloads it on change
glueqs_set() {
    python3 - "$HOME/.config/glueqs/settings.json" "$@" <<'PY' || die "glueqs_set $* failed"
import json, sys
p, kv = sys.argv[1], sys.argv[2:]
s = json.load(open(p))
for k, v in zip(kv[::2], kv[1::2]):
    s[k] = json.loads(v)
json.dump(s, open(p, "w"), indent=4)
PY
}

# Caelestia's drawers through its quickshell IPC (what glue-action calls)
caelestia_extras() {
    local q="qs -c caelestia ipc call"
    $q drawers toggle launcher
    sleep 1
    key f i r e
    sleep 2; snap launcher
    $q drawers toggle launcher; sleep 1
    $q drawers toggle dashboard
    sleep 3; snap dashboard
    $q drawers toggle dashboard; sleep 1
    notify-send -a "Glue Welcome" "Welcome to Glue Linux" "Caelestia on gluewc is ready."
    $q drawers toggle sidebar
    sleep 3; snap sidebar
    $q drawers toggle sidebar; sleep 1
    $q drawers toggle session
    sleep 3; snap session
    $q drawers toggle session; sleep 1
}

# Noctalia v5's own IPC (`noctalia msg`, docs/user/ipc in its sources)
noctalia_extras() {
    local ipc="noctalia msg"
    # launcher, query typed on the keyboard
    $ipc panel-toggle launcher
    sleep 1
    key f i r e
    sleep 2; snap launcher
    key Escape; sleep 1
    # control center, Home tab
    $ipc panel-open control-center home
    sleep 2; snap control-center
    $ipc panel-close; sleep 1
    # notifications: three real ones, then the control center's history tab
    notify-send -a "Glue Apps" "Updates installed" "12 packages were upgraded."
    notify-send -a "Firefox" "Download finished" "glue-linux-x86_64.iso"
    notify-send -a "Glue Welcome" "Welcome to Glue Linux" "Light. Fast. Yours."
    sleep 8   # toasts time out
    $ipc panel-open control-center notifications
    sleep 2; snap notifications
    $ipc panel-close; sleep 1
    # settings is a normal window: gluewc's float-centred bind (mod+v) lifts
    # it out of the tiling
    $ipc settings-open
    sleep 2
    key M+v
    sleep 2; snap settings
    $ipc settings-close; sleep 2
    # wallpaper picker over ~/Pictures/Wallpapers
    $ipc panel-toggle wallpaper
    sleep 3; snap wallpapers
    $ipc panel-close; sleep 1
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
    # cage on the headless backend, 1920x1080; gluewc runs inside it on the
    # wayland backend, so its seat gets cage's pointer and keyboard
    TARGET=$target OUTDIR=$outdir
    WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 WLR_LIBINPUT_NO_DEVICES=1 \
        WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128 cage -d -- sleep infinity >/tmp/cage.log 2>&1 &
    log "cage pid $!"
    poll "$TIMEOUT" "the cage wayland socket" wayland_socket_ready
    CAGE_DISPLAY=$WAYLAND_DISPLAY
    poll "$TIMEOUT" "cage output at ${WIDTH}x${HEIGHT}" \
        wlr-randr --output HEADLESS-1 --custom-mode "${WIDTH}x${HEIGHT}"
    # one virtual keyboard and pointer that live as long as the session: cage
    # (and so gluewc's seat) has keyboard/pointer from the start, instead of
    # dropping them each time a one-shot vkbd/vptr exits
    WAYLAND_DISPLAY=$CAGE_DISPLAY vkbd sleep2000000 >/dev/null 2>&1 &
    WAYLAND_DISPLAY=$CAGE_DISPLAY vptr sleep 2000000 >/dev/null 2>&1 &
    sleep 1
    mkdir -p /root/.config/gluewc
    cp /usr/share/gluewc/config.def.conf /root/.config/gluewc/config.conf
    sed -i 's/^root_color *= *.*/root_color     = E9E9E7/' /root/.config/gluewc/config.conf
    export XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=gluewc XDG_SESSION_DESKTOP=gluewc
    WLR_BACKENDS=wayland WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128 gluewc >/tmp/gluewc.log 2>&1 &
    log "gluewc pid $!"
    poll "$TIMEOUT" "the gluewc wayland socket" wayland_socket_ready "$CAGE_DISPLAY"
    log "cage on $CAGE_DISPLAY, gluewc on $WAYLAND_DISPLAY"
    case "$target" in
        glueqs)   shell_cmd="glueqs" ;;
        noctalia) shell_cmd="noctalia" ;;
        caelestia) shell_cmd="caelestia shell" ;;
    esac
    shell_log=/tmp/shell.log
    $shell_cmd >"$shell_log" 2>&1 &
    SHELL_PID=$!
    log "shell ($shell_cmd) pid $SHELL_PID"
    if [ "$target" = glueqs ]; then
        poll "$TIMEOUT" "glueqs 'Configuration Loaded'" grep -q "Configuration Loaded" "$shell_log"
    elif [ "$target" = caelestia ]; then
        poll "$TIMEOUT" "caelestia IPC" qs -c caelestia ipc show
    else
        poll "$TIMEOUT" "noctalia IPC" noctalia msg status
    fi
    kill -0 "$SHELL_PID" || die "shell exited: $(tail -n 20 "$shell_log")"
    sleep 8   # let the bar settle before windows open
    # Stillwater through the shell's own IPC (what glue-wallpaper-init does);
    # glueqs gets the copy in its picker folder so the picker marks it current
    case "$target" in
        glueqs)   poll "$TIMEOUT" "glueqs setwallpaper" qs -c glueqs ipc call glueqs setwallpaper "$WALLDIR/stillwater.png" ;;
        noctalia) poll "$TIMEOUT" "noctalia wallpaper set" noctalia msg wallpaper-set "$WALLDIR/stillwater.png" ;;
        caelestia) poll "$TIMEOUT" "caelestia wallpaper" caelestia wallpaper -f "$WALLPAPER" ;;
    esac
    sleep 4   # wallpaper decode + fade
    launch_clients wayland
    poll "$TIMEOUT" "alacritty + firefox under gluewc" gluewc_clients_ready
    sleep 8   # fastfetch output + firefox first paint
    log "gluewc-msg status: $(gluewc-msg status 2>&1 | head -n1)"
    snap bar
    # overview: a real tap on Super, exactly what the keypress handler waits for
    key Super
    sleep 5   # overview animation
    snap overview
    key Super
    sleep 3
    case "$target" in
        glueqs)   glueqs_extras ;;
        noctalia) noctalia_extras ;;
        caelestia) caelestia_extras ;;
    esac
    tail -n 5 "$shell_log" >&2 || true
}

case "${1:-all}" in
    --inside)  inside_main "${2:?target}" ;;
    --session) session_main "${2:?target}" "${3:?png}" ;;
    -h|--help) sed -n '2,/^set -eu$/{/^set -eu$/!p}' "$0"; exit 0 ;;
    *)         host_main "${1:-all}" ;;
esac
