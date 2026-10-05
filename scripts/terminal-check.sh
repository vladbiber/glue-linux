#!/bin/bash
# terminal-check.sh - probe, headless in the build container, that opening
# alacritty runs fastfetch with the Glue logo, fast enough.
#
#   sh scripts/terminal-check.sh
#
# Host side: one `docker run --rm --privileged --network host -v /dev/dri`
# of glue-pkgbuild-img with the repo on /glue. Exit code is non-zero when any
# probe fails; a PASS/FAIL table is printed at the end and the screenshot
# lands in screenshots/terminal.png.
#
# Inside (--inside, root): installs alacritty/fastfetch/grim, builds and
# installs glue-branding, creates the user `glue` with the REAL /etc/skel and
# the .bashrc/.zshrc taken from plan.py, then re-executes as that user
# (--probes) and prints the table:
#   fastfetch-config-path   the skel config is on fastfetch's search path
#                           (/etc/xdg/fastfetch too: survives rm -r ~/.config)
#   fastfetch-logo          >= 3 logo lines + the #F1B00A truecolor sequence
#   fastfetch-os            "Glue Linux" on the OS line, no "artix" anywhere
#   fastfetch-latency       median of 7 runs <= 100 ms
#   alacritty-xdg-config    without ~/.config/alacritty the /etc/xdg file loads,
#                           with it the ~/.config one does
#   alacritty-fastfetch-shot  `alacritty -e bash -i` under gluewc headless
#                           -> grim -> PNG 1920x1080 with the amber logo pixels
#   termfont                +N / -N / reset edit `size` in the user's toml
set -u

IMAGE="${GLUE_CHECK_IMAGE:-glue-pkgbuild-img}"
TIMEOUT=60
RUNS=7
MAX_MS=100
RESULTS=/tmp/terminal-check.results
FF_LOG=/tmp/terminal-check.fastfetch.log
LOGDIR=/tmp/terminal-check.d   # per-run, owned by the test user (the image's /tmp has root-owned leftovers)

log() { printf '[terminal-check] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# ---------------------------------------------------------------- host ----
host_main() {
    local root start rc=0
    root=$(cd -- "$(dirname -- "$0")/.." && pwd)
    command -v docker >/dev/null 2>&1 || die "docker not found"
    [ -e /dev/dri/renderD128 ] || die "/dev/dri/renderD128 missing: scenefx has no software renderer"
    docker image inspect "$IMAGE" >/dev/null 2>&1 || die "docker image $IMAGE not found (run ./build.sh first)"
    start=$(date +%s)
    docker run --rm --privileged --network host \
        -v /dev/dri:/dev/dri -v "$root:/glue" -w /glue \
        "$IMAGE" /bin/bash /glue/scripts/terminal-check.sh --inside || rc=$?
    log "done in $(( $(date +%s) - start )) s (exit $rc)"
    return $rc
}

# ------------------------------------------------------------- container ----
build_local() { # build_local NAME - makepkg as builder, then pacman -U (same recipe as bar-runtime-check.sh)
    local name=$1 pkg
    log "building $name with makepkg"
    rm -r -f "/tmp/build-$name"
    cp -r "/glue/packages/$name" "/tmp/build-$name"
    rm -f "/tmp/build-$name"/*.pkg.tar.*
    chown -R builder:builder "/tmp/build-$name"
    ( cd "/tmp/build-$name" && sudo -u builder makepkg -f --syncdeps --noconfirm --skippgpcheck ) >/tmp/makepkg.log 2>&1 \
        || { tail -n 20 /tmp/makepkg.log >&2; die "makepkg $name failed"; }
    pkg=$(ls -1 "/tmp/build-$name"/*.pkg.tar.* | head -n1)
    pacman -U --noconfirm --needed "$pkg" >/dev/null
}

plan_content() { # plan_content _BASHRC_CONTENT - the REAL string from plan.py
    PYTHONPATH=/glue/packages/glue-installer python3 -c \
        "import sys; from glue_installer import plan; sys.stdout.write(plan.$1)"
}

inside_main() {
    local rdgid rc=0 pkg
    [ -w /glue/screenshots ] || die "/glue/screenshots is not writable"
    grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
    log "syncing package databases"
    pacman -Sy --noconfirm >/dev/null
    pacman -S --noconfirm --needed alacritty fastfetch ttf-liberation grim bash zsh \
        imagemagick python-pillow >/dev/null 2>&1
    pacman -Q alacritty fastfetch grim ttf-liberation >/dev/null 2>&1 || die "alacritty/fastfetch/grim/ttf-liberation not installable"
    if ! pacman -Q gluewc >/dev/null 2>&1; then
        pkg=$(ls -1 /glue/packages/gluewc/*.pkg.tar.* /home/builder/pkgs/gluewc-*.pkg.tar.* 2>/dev/null | head -n1 || true)
        if [ -n "$pkg" ]; then pacman -U --noconfirm --needed "$pkg" >/dev/null; else build_local gluewc; fi
    fi
    build_local glue-branding
    # on a real system /etc/os-release is a symlink to ../usr/lib/os-release
    # (filesystem package); in the container image it is a stale regular file
    # still saying Artix, so restore the symlink the target has
    ln -sf ../usr/lib/os-release /etc/os-release
    # the test user: real skel, real bashrc/zshrc from plan.py (not a copy)
    id glue >/dev/null 2>&1 || useradd -m -k /etc/skel -s /bin/bash glue
    plan_content _BASHRC_CONTENT > /home/glue/.bashrc
    plan_content _ZSHRC_CONTENT > /home/glue/.zshrc
    [ -s /home/glue/.bashrc ] && [ -s /home/glue/.zshrc ] || die "could not extract .bashrc/.zshrc from plan.py"
    chown glue:glue /home/glue/.bashrc /home/glue/.zshrc
    # scenefx wants the DRM render node; its gid is the host's, mirror it
    rdgid=$(stat -c %g /dev/dri/renderD128)
    getent group "$rdgid" >/dev/null || groupadd -g "$rdgid" hostrender
    usermod -aG "$(getent group "$rdgid" | cut -d: -f1)" glue
    rm -r -f /tmp/xdg-glue; install -d -m 700 -o glue -g glue /tmp/xdg-glue
    rm -r -f "$LOGDIR"; install -d -o glue -g glue "$LOGDIR"
    : > "$RESULTS"; : > "$FF_LOG"; chmod 666 "$RESULTS" "$FF_LOG"
    runuser -u glue -- env HOME=/home/glue XDG_RUNTIME_DIR=/tmp/xdg-glue \
        /bin/bash /glue/scripts/terminal-check.sh --probes || rc=$?
    printf '\n== terminal: alacritty + fastfetch probes ==\n%s\n' "$(cat "$RESULTS")"
    printf '\n== fastfetch output (as user glue) ==\n'
    sed 's/\x1b\[[0-9;?]*[A-Za-z]//g' "$FF_LOG" | head -n 40
    # the PNG is copied by root: the test user cannot write the bind mount
    if [ -s "$LOGDIR/terminal.png" ]; then cp "$LOGDIR/terminal.png" /glue/screenshots/terminal.png || rc=1
    else log "no screenshot was taken"; rc=1; fi
    ! grep -q ' FAIL ' "$RESULTS" || rc=1
    [ "$(grep -c -E ' (PASS|FAIL) ' "$RESULTS")" -ge 7 ] || { log "fewer than 7 probes recorded"; rc=1; }
    return $rc
}

# ---------------------------------------------------- probes (as user glue) ----
record() { # record NAME PASS|FAIL "detail"
    printf '%-26s %-4s %s\n' "$1" "$2" "$3" >> "$RESULTS"
}
strip_ansi() { sed 's/\x1b\[[0-9;?]*[A-Za-z]//g'; }
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
ff() { TERM=xterm-256color COLORTERM=truecolor fastfetch --pipe false "$@" 2>&1; }
AMBER=$'\e[38;2;241;176;10m' # fastfetch --pipe false: logo color "#F1B00A" as truecolor

probe_config_path() {
    local paths ok=1 detail="~/.config + /etc/xdg/fastfetch on the search path, both the Glue config"
    paths=$(fastfetch --list-config-paths 2>&1)
    printf '%s\n' "$paths" | grep -q "^$HOME/.config/fastfetch/" || { ok=0; detail="skel dir not on the search path"; }
    cmp -s /etc/glue/fastfetch.jsonc "$HOME/.config/fastfetch/config.jsonc" || { ok=0; detail="skel config missing/different"; }
    printf '%s\n' "$paths" | grep -q '^/etc/xdg/fastfetch/' || { ok=0; detail="/etc/xdg/fastfetch not on the search path"; }
    cmp -s /etc/glue/fastfetch.jsonc /etc/xdg/fastfetch/config.jsonc || { ok=0; detail="/etc/xdg/fastfetch/config.jsonc missing/different"; }
    # the /etc/xdg copy alone must still give the Glue logo
    mv "$HOME/.config/fastfetch" "$HOME/.config/fastfetch.off"
    ff | grep -qF "$AMBER" || { ok=0; detail="without ~/.config/fastfetch the logo is not amber"; }
    mv "$HOME/.config/fastfetch.off" "$HOME/.config/fastfetch"
    if [ $ok -eq 1 ]; then record fastfetch-config-path PASS "$detail"
    else record fastfetch-config-path FAIL "$detail"; fi
}

probe_logo() {
    local plain n=0 amber line
    ff > "$FF_LOG"
    plain=$(strip_ansi < "$FF_LOG" | sed -E 's/^[[:space:]]+//')
    while IFS= read -r line; do
        line=$(printf '%s' "$line" | sed -E 's/\$[1-4]//g; s/^[[:space:]]+//; s/[[:space:]]+$//')
        [ ${#line} -ge 3 ] || continue
        printf '%s\n' "$plain" | grep -qF -- "$line" && n=$((n + 1))
    done < /usr/share/glue/logo.txt
    amber=$(grep -cF "$AMBER" "$FF_LOG")
    if [ "$n" -ge 3 ] && [ "$amber" -ge 1 ]; then record fastfetch-logo PASS "$n logo lines, truecolor F1B00A on $amber lines"
    else record fastfetch-logo FAIL "logo lines matched: $n, F1B00A lines: $amber"; fi
}

probe_os() {
    local plain osline art
    plain=$(strip_ansi < "$FF_LOG")
    osline=$(printf '%s\n' "$plain" | grep -E 'OS *->' | head -n1)
    art=$(printf '%s\n' "$plain" | grep -ci artix)
    if printf '%s' "$osline" | grep -q 'Glue Linux' && [ "$art" -eq 0 ]; then
        record fastfetch-os PASS "$(printf '%s' "$osline" | sed 's/^.*OS/OS/' | cut -c1-50)"
    else
        record fastfetch-os FAIL "OS line: '$(printf '%s' "$osline" | cut -c1-40)', artix lines: $art"
    fi
}

probe_latency() {
    local i t0 t1 ms="" med
    ff >/dev/null  # warm the page cache
    for i in $(seq 1 $RUNS); do
        t0=$(date +%s%N)
        TERM=xterm-256color COLORTERM=truecolor fastfetch --pipe false >/dev/null 2>&1
        t1=$(date +%s%N)
        ms="$ms $(( (t1 - t0) / 1000000 ))"
    done
    med=$(printf '%s\n' $ms | sort -n | sed -n "$(( (RUNS + 1) / 2 ))p")
    if [ "$med" -le "$MAX_MS" ]; then record fastfetch-latency PASS "median ${med} ms (runs:$ms)"
    else record fastfetch-latency FAIL "median ${med} ms > ${MAX_MS} (runs:$ms)"; fi
    log "fastfetch median ${med} ms (runs:$ms)"
}

start_gluewc() {
    mkdir -p "$HOME/.config/gluewc"
    cp /usr/share/gluewc/config.def.conf "$HOME/.config/gluewc/config.conf"
    printf '\noutput = HEADLESS-1 mode=1920x1080@60\nroot_color = 100A02\n' >> "$HOME/.config/gluewc/config.conf"
    WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128 \
        gluewc >$LOGDIR/gluewc.log 2>&1 &
    poll "$TIMEOUT" "the gluewc wayland socket" wayland_socket_ready || { tail -n 5 $LOGDIR/gluewc.log >&2; return 1; }
    log "WAYLAND_DISPLAY=$WAYLAND_DISPLAY"
}

clients() { # toplevel clients over all tags: field 4 of `gluewc-msg status` is "n,n,n,..." per tag
    gluewc-msg status 2>/dev/null | head -n1 | awk '{ n = split($4, t, ","); for (i = 1; i <= n; i++) s += t[i] } END { print s + 0 }'
}

client_added() { [ "$(clients)" -gt "$1" ]; }

run_alacritty_log() { # run_alacritty_log LOGFILE - start alacritty -vv briefly, kill it
    local pid
    alacritty -vv -e sleep 8 >"$1" 2>&1 &
    pid=$!
    sleep 4; kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
    return 0
}

probe_alacritty_config() {
    local cfgdir="$HOME/.config/alacritty" ok=1 detail=""
    mv "$cfgdir" "$cfgdir.off"
    run_alacritty_log $LOGDIR/ala1.log
    grep -q '/etc/xdg/alacritty/alacritty.toml' $LOGDIR/ala1.log || { ok=0; detail="no /etc/xdg load: $(grep -i -m1 'config' $LOGDIR/ala1.log | cut -c1-50)"; }
    mv "$cfgdir.off" "$cfgdir"
    run_alacritty_log $LOGDIR/ala2.log
    grep -q "$cfgdir/alacritty.toml" $LOGDIR/ala2.log || { ok=0; detail="$detail; no ~/.config load"; }
    if [ $ok -eq 1 ]; then record alacritty-xdg-config PASS "/etc/xdg without ~/.config, ~/.config otherwise"
    else record alacritty-xdg-config FAIL "$detail"; fi
}

check_png() { # check_png FILE - rc 0 when 1920x1080, >300 colors, both amber tones
    python3 - "$1" <<'PY'
import sys
from PIL import Image
im = Image.open(sys.argv[1]).convert("RGB")
w, h = im.size
colors = im.getcolors(maxcolors=w * h) or []
def near(c, t): return all(abs(a - b) <= 10 for a, b in zip(c, t))
a1 = sum(n for n, c in colors if near(c, (0xF1, 0xB0, 0x0A)))
a2 = sum(n for n, c in colors if near(c, (0xA6, 0x69, 0x00)))
print("%dx%d colors=%d F1B00A=%d A66900=%d" % (w, h, len(colors), a1, a2))
sys.exit(0 if (w, h) == (1920, 1080) and len(colors) > 300 and a1 > 0 and a2 > 0 else 1)
PY
}

probe_shot() {
    local pid before detail tmp=$LOGDIR/terminal.png
    before=$(clients)
    # interactive bash: goes through the real ~/.bashrc, which runs fastfetch
    alacritty -e bash -i >$LOGDIR/ala-shot.log 2>&1 &
    pid=$!
    poll 30 "the alacritty client in gluewc-msg status" client_added "$before" || \
        { record alacritty-fastfetch-shot FAIL "alacritty never appeared in gluewc-msg status"; kill "$pid" 2>/dev/null; return; }
    sleep 4
    if ! kill -0 "$pid" 2>/dev/null; then
        record alacritty-fastfetch-shot FAIL "alacritty exited: $(tail -n1 $LOGDIR/ala-shot.log | cut -c1-50)"; return
    fi
    if ! grim "$tmp" 2>$LOGDIR/grim.log; then
        record alacritty-fastfetch-shot FAIL "grim: $(tail -n1 $LOGDIR/grim.log | cut -c1-50)"; kill "$pid"; return
    fi
    kill "$pid" 2>/dev/null
    detail=$(check_png "$tmp"); local rc=$?
    if [ $rc -eq 0 ]; then record alacritty-fastfetch-shot PASS "$detail"
    else record alacritty-fastfetch-shot FAIL "$detail"; fi
}

toml_size() { awk '/^\[font\]/ {f=1; next} /^\[/ {f=0} f && /^size/ {gsub(/[^0-9.]/, ""); print}' "$HOME/.config/alacritty/alacritty.toml"; }

probe_termfont() {
    local s0 s1 s2 s3 ok=1
    s0=$(toml_size)
    termfont +2 >/dev/null; s1=$(toml_size)
    termfont -1 >/dev/null; s2=$(toml_size)
    termfont reset >/dev/null; s3=$(toml_size)
    awk -v a="$s0" -v b="$s1" -v c="$s2" -v d="$s3" \
        'BEGIN { exit !(a == 11 && b == 13 && c == 12 && d == 11) }' || ok=0
    # nothing but `size` changed (reset writes "size = 11.0")
    cmp -s /etc/xdg/alacritty/alacritty.toml "$HOME/.config/alacritty/alacritty.toml" || ok=0
    if [ $ok -eq 1 ]; then record termfont PASS "$s0 -> +2 = $s1 -> -1 = $s2 -> reset = $s3"
    else record termfont FAIL "sizes $s0 / $s1 / $s2 / $s3 (expected 11/13/12/11, file unchanged otherwise)"; fi
}

probes_main() {
    export LIBGL_ALWAYS_SOFTWARE=1 GLUE_WELCOME_DISABLED=1
    export XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=gluewc
    trap 'kill $(jobs -p) 2>/dev/null' EXIT
    probe_config_path
    probe_logo
    probe_os
    probe_latency
    probe_termfont
    if start_gluewc; then
        probe_alacritty_config
        probe_shot
    else
        record alacritty-xdg-config FAIL "gluewc did not start"
        record alacritty-fastfetch-shot FAIL "gluewc did not start"
    fi
    return 0
}

case "${1:-}" in
    --inside)  inside_main ;;
    --probes)  probes_main ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    "")        host_main ;;
    *)         die "unknown argument '$1'" ;;
esac
