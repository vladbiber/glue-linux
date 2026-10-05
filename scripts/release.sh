#!/usr/bin/env bash
# Rebuild some [glue] packages and publish them to the online repository.
#   scripts/release.sh gluewc glueqs
# Packages built from git (pkgver()) get a new version from their new commits;
# the others need a pkgrel bump, which is checked before anything is built.
set -euo pipefail
cd "$(dirname "$0")/.."

GH_REPO=${GH_REPO:-vladbiber/glue-repo}
REPO=.glue-repo
DB_URL="https://github.com/$GH_REPO/releases/download/x86_64/glue.db"

[ $# -gt 0 ] || { echo "usage: $0 package..." >&2; exit 1; }

published() { ls "$REPO/$1"-[0-9r]*.pkg.tar.zst 2>/dev/null | sed "s|^$REPO/||; s|-[^-]*\.pkg\.tar\.zst$||" | head -n1; }

fail=0
for p in "$@"; do
    pb=packages/$p/PKGBUILD
    [ -f "$pb" ] || { echo "$p: no $pb" >&2; fail=1; continue; }
    old=$(published "$p")
    if grep -q '^pkgver()' "$pb"; then
        url=$(sed -n 's|^source=("git+\([^"#]*\).*|\1|p' "$pb")
        head=$(git ls-remote "$url" HEAD | cut -c1-7)
        case "$old" in
            *".$head-"*) echo "$p: $url has no commits since $old, push first" >&2; fail=1 ;;
        esac
    else
        v=$(bash -c 'source "$1" >/dev/null 2>&1; echo "$pkgname-$pkgver-$pkgrel"' _ "$pb")
        if [ "$v" = "$old" ]; then
            echo "$p: $old is already published, bump pkgrel in $pb" >&2; fail=1
        fi
    fi
done
[ "$fail" = 0 ] || exit 1

GLUE_PKGS="$*" GLUE_REPO_ONLY=1 ./build.sh
sh scripts/publish-repo.sh

want=()
for p in "$@"; do want+=("$(published "$p")"); done
echo "waiting for GitHub to serve the new database..."
for _ in $(seq 60); do
    names=$(curl -fsSL "$DB_URL" 2>/dev/null | tar -tz 2>/dev/null || true)
    missing=0
    for w in "${want[@]}"; do
        grep -qx "$w/" <<<"$names" || missing=1
    done
    if [ "$missing" = 0 ]; then
        printf 'online: %s\n' "${want[@]}"
        echo "users get it with the next system update"
        exit 0
    fi
    sleep 10
done
echo "uploaded, but GitHub still serves the old database after 10 minutes" >&2
exit 1
