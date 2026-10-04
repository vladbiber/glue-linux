#!/bin/sh
# calamares-probe.sh: start Artix Calamares with the Glue config on headless gluewc
# in an unprivileged container (render node only, no disks) and save screenshots.
#   sh scripts/calamares-probe.sh [outdir]
# Image: glue-pkgbuild-img + calamares kpmcore ckbcomp os-prober wtype grim.
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$REPO/screenshots/calamares}
IMAGE=${GLUE_CALA_IMAGE:-glue-cala-img}
mkdir -p "$OUT"
CFG=$(mktemp -d)
cp -r "$REPO/packages/glue-calamares-config/." "$CFG/"
cp "$REPO/packages/glue-welcome/data/icons/org.glue.Welcome.svg" "$CFG/branding/glue/logo.svg"
cp "$REPO/packages/glue-branding/wallpaper.png" "$CFG/branding/glue/welcome.png"
ln -s /usr/share/calamares/qml "$CFG/qml"
docker run --rm --network host --device /dev/dri/renderD128 \
    -v "$CFG:/cfg:ro" -v "$OUT:/out" "$IMAGE" sh -c '
set -u
export XDG_RUNTIME_DIR=/tmp/rt WLR_BACKENDS=headless WLR_RENDERER=pixman \
       WLR_HEADLESS_OUTPUTS=1 WLR_LIBINPUT_NO_DEVICES=1
mkdir -p -m 700 $XDG_RUNTIME_DIR /run/dbus
[ -f /etc/machine-id ] || dbus-uuidgen > /etc/machine-id
dbus-daemon --system --fork
/usr/lib/polkit-1/polkitd --no-debug >/dev/null 2>&1 &
/usr/lib/kpmcore_externalcommand >/dev/null 2>&1 &
gluewc >/tmp/wc.log 2>&1 &
for i in $(seq 50); do sock=$(ls $XDG_RUNTIME_DIR | grep -m1 "^wayland-[0-9]*$" || true); [ -n "$sock" ] && break; sleep 0.2; done
[ -n "$sock" ] || { tail /tmp/wc.log; exit 1; }
WAYLAND_DISPLAY=$sock QT_QPA_PLATFORM=wayland dbus-run-session calamares -d -c /cfg >/out/calamares.log 2>&1 &
sleep 12
WAYLAND_DISPLAY=$sock grim /out/welcome.png
for m in welcome locale keyboard partition users summary finished; do
    grep -q "ViewModule \"$m@$m\" loading complete" /out/calamares.log \
        && echo "module $m: loaded" || { echo "module $m: NOT loaded"; fail=1; }
done
exit ${fail:-0}'
rm -rf "$CFG"
echo "screenshots + log in $OUT"
