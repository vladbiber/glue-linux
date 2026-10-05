#!/bin/sh
# calamares-probe.sh: start Artix Calamares with the Glue config on headless gluewc
# in an unprivileged container (render node only, no disks) and save screenshots.
#   sh scripts/calamares-probe.sh [outdir]
# Image: glue-pkgbuild-img + calamares kpmcore ckbcomp os-prober wtype grim.
# The config is mounted at /etc/calamares (modules-search lists /etc/calamares/modules).
set -eu
REPO=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:-$REPO/screenshots/calamares}
IMAGE=${GLUE_CALA_IMAGE:-glue-cala-img}
mkdir -p "$OUT"
CFG=$(mktemp -d)
cp -r "$REPO/packages/glue-calamares-config/." "$CFG/"
cp "$REPO/packages/glue-welcome/data/icons/org.glue.Welcome.svg" "$CFG/branding/glue/logo.svg"
cp "$REPO/packages/glue-branding/wallpaper.png" "$CFG/branding/glue/welcome.png"
# same layout as the PKGBUILD: the QML pages live in the branding dir
cp "$REPO"/packages/glue-calamares-config/modules/gluedesktop/* \
   "$REPO"/packages/glue-calamares-config/modules/gluenetwork/* \
   "$REPO"/packages/glue-calamares-config/modules/gluechoice/* "$CFG/branding/glue/"
ln -s /usr/share/calamares/qml "$CFG/qml"
# GLUE_PROBE_FIRST=<step>: show that page first (no input reaches headless gluewc)
if [ -n "${GLUE_PROBE_FIRST:-}" ]; then
    sed -i "0,/^  - welcome$/s//  - $GLUE_PROBE_FIRST\n  - welcome/" "$CFG/settings.conf"
fi
docker run --rm --network host --device /dev/dri/renderD128 \
    -v "$CFG:/etc/calamares:ro" -v "$REPO/packages/glue-installer:/usr/lib/glue-installer:ro" \
    -v "$REPO/packages/glue-installer/catalog:/usr/share/glue-installer/catalog:ro" \
    -v "$OUT:/out" "$IMAGE" sh -c '
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
WAYLAND_DISPLAY=$sock QT_QPA_PLATFORM=wayland dbus-run-session calamares -d >/out/calamares.log 2>&1 &
sleep 12
WAYLAND_DISPLAY=$sock grim /out/welcome.png
for m in welcome locale keyboard partition users summary finished \
         notesqml@gluenetwork notesqml@gluedesktop notesqml@gluekernel \
         notesqml@glueinit notesqml@gluegaming; do
    case $m in *@*) inst=$m ;; *) inst="$m@$m" ;; esac
    grep -q "ViewModule \"$inst\" loading complete" /out/calamares.log \
        && echo "module $m: loaded" || { echo "module $m: NOT loaded"; fail=1; }
done
if grep -q "STARTUP: failed modules" /out/calamares.log; then
    grep "STARTUP: failed modules" /out/calamares.log; fail=1
else echo "job modules glueinstall gluefstab: found"; fi
exit ${fail:-0}'
rm -rf "$CFG"
echo "screenshots + log in $OUT"
