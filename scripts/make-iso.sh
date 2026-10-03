#!/bin/bash
# make-iso.sh — runs INSIDE the Artix build container (see Dockerfile/build.sh).
# 1) builds the custom [glue] package repo from packages/
# 2) assembles the artools iso profile
# 3) runs buildiso to produce the ISO into /out
set -euo pipefail

ROOT=/glue
REPO=$ROOT/repo/x86_64           # local pacman repo (db + packages)
OUT=/out

echo ">>> [1/4] refresh keyrings"
pacman -Sy --noconfirm --needed artix-keyring cachyos-keyring || true
pacman-key --populate artix cachyos 2>/dev/null || true

echo ">>> [2/4] build custom packages -> [glue] repo"
mkdir -p "$REPO"
# stage pacman.conf next to the installer PKGBUILD (makepkg can't take ../.. paths)
cp "$ROOT/repo/pacman.conf" "$ROOT/packages/glue-installer/pacman.conf"
chown -R builder:builder "$ROOT/repo" "$ROOT/packages"
# Register the (initially empty) [glue] repo with the build container's
# pacman BEFORE building: later packages in the loop depend on earlier ones
# (imperative-qs needs swww + matugen), and makepkg --syncdeps resolves deps
# through pacman. Refresh the repo db after each package.
repo-add -q "$REPO/glue.db.tar.gz" 2>/dev/null || true
grep -q '^\[glue\]' /etc/pacman.conf || cat >> /etc/pacman.conf <<EOF

[glue]
SigLevel = Optional TrustAll
Server = file://$REPO
EOF
pacman -Sy --noconfirm >/dev/null 2>&1 || true

# quickshell-based bars (glue-bar, imperative-qs + its swww/matugen deps)
# are no longer built: quickshell was dropped from the catalog and the ISO.
# order matters: lib32-mangohud needs lib32-glew, glueqs needs gluewc, both from [glue]
for pkg in glue-branding glue-settings st-glue nvwm proton-ge-custom-bin \
           scx-scheds ananicy-cpp lib32-glew lib32-mangohud cage gluewc glueqs \
           glue-apps glue-welcome glue-installer; do
    echo "    -- $pkg"
    ( cd "$ROOT/packages/$pkg" && \
      sudo -u builder makepkg -f --syncdeps --noconfirm --skippgpcheck )
    cp "$ROOT/packages/$pkg"/*.pkg.tar.* "$REPO"/ 2>/dev/null || true
    repo-add -q "$REPO/glue.db.tar.gz" "$REPO"/*.pkg.tar.* >/dev/null 2>&1 || true
    pacman -Sy --noconfirm >/dev/null 2>&1 || true
done
repo-add "$REPO/glue.db.tar.gz" "$REPO"/*.pkg.tar.* 2>/dev/null || \
    repo-add "$REPO/glue.db.tar.zst" "$REPO"/*.pkg.tar.*

# make the [glue] repo visible to buildiso's pacman
install -Dm644 "$ROOT/repo/pacman.conf" /etc/pacman.conf
sed -i "s|file:///usr/share/glue/repo|file://$REPO|" /etc/pacman.conf
# build-host only: keep pacman's scriptlet sandbox off (this is the container's
# config, not the target's — the installed system keeps the stock sandbox).
grep -q '^DisableSandbox' /etc/pacman.conf || sed -i '/^\[options\]/a DisableSandbox' /etc/pacman.conf
pacman -Sy --noconfirm || true

# buildiso installs the live rootfs with ITS OWN pacman config
# (/usr/share/artools/pacman.conf.d/iso-*-x86_64.conf), not /etc/pacman.conf —
# so our custom [glue] repo and [cachyos] must be added there too, or the
# rootfs install fails with "target not found". SigLevel is relaxed for the
# build only; the installed target keeps proper signature checking.
for isoconf in /usr/share/artools/pacman.conf.d/iso*-x86_64.conf; do
    [ -f "$isoconf" ] || continue
    grep -q '^\[glue\]' "$isoconf" && continue
    cat >> "$isoconf" <<EOF

[cachyos]
SigLevel = Optional TrustAll
Include = /etc/pacman.d/cachyos-mirrorlist

[glue]
SigLevel = Optional TrustAll
Server = file://$REPO
EOF
done

echo ">>> [3/4] assemble iso profile"
# start from the official artix iso-profiles, layer our 'glue' profile on top
PROFILES=/usr/share/artools/iso-profiles
[ -d "$PROFILES" ] || PROFILES=$(buildiso -q 2>/dev/null; echo /usr/share/artools/iso-profiles)
git clone --depth=1 https://gitea.artixlinux.org/artix/iso-profiles "$PROFILES" 2>/dev/null || true
cp -r "$ROOT/iso-profile/glue" "$PROFILES/glue"

# carry the built repo + pacman.conf into the live root so the installed
# system (and the live installer) can pull our packages
DEST="$PROFILES/glue/root-overlay/usr/share/glue"
mkdir -p "$DEST"
cp -a "$REPO" "$DEST/repo"
install -Dm644 "$ROOT/repo/pacman.conf" "$DEST/pacman.conf"

echo ">>> [4/4] buildiso"
mkdir -p "$OUT"
# Write the finished ISO under the bind-mounted /var/lib/artools so it survives
# the --rm container (artools' default ISO_POOL is $HOME/artools-workspace/iso,
# which is ephemeral). buildiso reads ISO_POOL from artools-iso.conf.
ISO_POOL=/var/lib/artools/iso
mkdir -p "$ISO_POOL"
if grep -q '^[#[:space:]]*ISO_POOL=' /etc/artools/artools-iso.conf; then
    sed -i "s|^[#[:space:]]*ISO_POOL=.*|ISO_POOL=$ISO_POOL|" /etc/artools/artools-iso.conf
else
    echo "ISO_POOL=$ISO_POOL" >> /etc/artools/artools-iso.conf
fi
# De-Artix the ISO identity: buildiso hardcodes the volume label "ARTIX_YYYYMM"
# and prefixes the filename with "artix". Label -> GLUE_, and drop the
# prefix so the file is just "glue-runit-...". (appid / publisher already
# come from our Glue os-release.)
sed -i 's/iso_label="ARTIX_/iso_label="GLUE_/' /usr/bin/buildiso
sed -i 's/local vars=("artix")/local vars=()/' /usr/bin/buildiso

# clear stale ISOs from the (persistent) pool so only this build's ISO remains
rm -f "$ISO_POOL"/glue/*.iso 2>/dev/null || true

buildiso -p glue -i runit || {
    echo "!! buildiso failed — check artools version / profile keys" >&2
    exit 1
}
# start with a clean out/ so old/renamed ISOs don't pile up and confuse which
# file to flash, then copy this build's ISO out
rm -f "$OUT"/*.iso 2>/dev/null || true
find "$ISO_POOL" /var/lib/artools /root/artools-workspace /var/cache/artools \
    -name '*.iso' -exec cp -v {} "$OUT/" \; 2>/dev/null || true
echo ">>> ISO(s) in $OUT:"; ls -lh "$OUT" || true
