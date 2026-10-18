#!/usr/bin/env bash
# build.sh - build the Glue Linux ISO on any host with Docker.
# Spins an Artix build container (artools + CachyOS repo), builds the custom
# package repo, and runs buildiso. The finished ISO lands in ./out/.
set -euo pipefail
cd "$(dirname "$0")"

IMAGE=glue-build
OUT="$PWD/out"
# buildiso layers livefs over rootfs with overlayfs. Its workdir must live on a
# real disk fs (ext4) that supports being an overlayfs upperdir - Docker's own
# overlay2 rootfs does NOT (overlay-on-overlay is rejected), so we bind-mount a
# host dir. .pkgcache persists the package cache so re-runs don't re-download.
WORK="$PWD/.artools-work"
PKGCACHE="$PWD/.pkgcache"
# built [glue] packages persist here; GLUE_PKGS="a b" rebuilds only those
GLUEREPO="$PWD/.glue-repo"
mkdir -p "$OUT"

echo ">>> building image '$IMAGE' (Artix + artools + CachyOS)"
# --network=host: this host has no default docker 'bridge' network
docker build --network=host -t "$IMAGE" .

# created after the build so they don't bloat the build context
mkdir -p "$WORK" "$PKGCACHE" "$GLUEREPO"

echo ">>> running ISO build (privileged — needs loop devices / overlayfs)"
RUN_TTY=""; [ -t 1 ] && RUN_TTY="-it"   # only attach a TTY when interactive
# packages and the [glue] database are signed with this key when it exists
SIGNHOME=${GLUE_SIGN_HOME:-$HOME/.local/share/glue-signing}
SIGN_MOUNT=""
[ -d "$SIGNHOME" ] && SIGN_MOUNT="-v $SIGNHOME:/glue-sign:ro"
# gluemac and the MacShell fork have no remote yet: their PKGBUILDs clone /src/<name>
SRC_MOUNTS=""
for src in gluemac gluemac-macshell; do
    [ -d "$HOME/$src/.git" ] && SRC_MOUNTS="$SRC_MOUNTS -v $HOME/$src:/src/$src:ro"
done
# GLUEWC_LOCAL=1 builds gluewc from ~/gluewc instead of its GitHub remote
GLUEWC_ENV=""
if [ "${GLUEWC_LOCAL:-0}" = 1 ] && [ -d "$HOME/gluewc/.git" ]; then
    SRC_MOUNTS="$SRC_MOUNTS -v $HOME/gluewc:/src/gluewc:ro"
    GLUEWC_ENV="-e GLUEWC_SRC=file:///src/gluewc"
fi

docker run --rm $RUN_TTY \
    --privileged \
    --network=host \
    -v /dev:/dev \
    -v "$OUT:/out" \
    -v "$WORK:/var/lib/artools" \
    -v "$PKGCACHE:/var/cache/pacman/pkg" \
    -v "$GLUEREPO:/glue/repo/x86_64" \
    $SIGN_MOUNT $SRC_MOUNTS \
    -e GLUE_PKGS="${GLUE_PKGS:-}" \
    $GLUEWC_ENV \
    -e GLUE_REPO_ONLY="${GLUE_REPO_ONLY:-0}" \
    "$IMAGE"

echo
echo ">>> done. ISO(s):"
ls -lh "$OUT"
echo
echo "Write it to a USB stick with, e.g.:"
echo "    sudo dd if=out/<file>.iso of=/dev/sdX bs=4M status=progress oflag=sync"
