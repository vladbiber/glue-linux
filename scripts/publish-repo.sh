#!/bin/sh
# Upload the [glue] repository built by build.sh to GitHub Releases.
# pacman reads it from https://github.com/$GH_REPO/releases/download/x86_64/
# (release assets, because some packages are larger than git allows).
# Only new or changed files are uploaded; assets gone from the local repo
# are deleted. Needs `gh auth login` once.
set -eu

GH_REPO=${GH_REPO:-vladbiber/glue-repo}
TAG=x86_64
DIR=${1:-"$(dirname "$0")/../.glue-repo"}
cd "$DIR"

[ -f glue.db.tar.gz ] || { echo "no glue.db.tar.gz in $DIR, run build.sh first" >&2; exit 1; }
[ -f glue.db.tar.gz.sig ] || { echo "the database is not signed, see build.sh" >&2; exit 1; }
for f in *.pkg.tar.zst; do
    [ -f "$f.sig" ] || { echo "$f is not signed" >&2; exit 1; }
done

if ! gh repo view "$GH_REPO" >/dev/null 2>&1; then
    gh repo create "$GH_REPO" --public --add-readme \
        --description "Package repository of Glue Linux"
fi
if ! gh release view "$TAG" -R "$GH_REPO" >/dev/null 2>&1; then
    gh release create "$TAG" -R "$GH_REPO" --title "Glue Linux packages (x86_64)" \
        --notes "The [glue] pacman repository. Installed systems use it through
/etc/pacman.conf; nothing here needs to be downloaded by hand."
fi

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
# pacman asks for glue.db and glue.files, release assets cannot be symlinks
cp glue.db.tar.gz "$stage/glue.db"
cp glue.db.tar.gz.sig "$stage/glue.db.sig"
cp glue.files.tar.gz "$stage/glue.files"
[ -f glue.files.tar.gz.sig ] && cp glue.files.tar.gz.sig "$stage/glue.files.sig"

remote=$(gh release view "$TAG" -R "$GH_REPO" --json assets \
    --jq '.assets[] | "\(.name) \(.size)"')
size_of() { printf '%s\n' "$remote" | awk -v n="$1" '$1 == n { print $2 }'; }

for f in *.pkg.tar.zst *.pkg.tar.zst.sig; do
    if [ "$(size_of "$f")" = "$(stat -c %s "$f")" ]; then
        continue
    fi
    echo "upload $f"
    gh release upload "$TAG" -R "$GH_REPO" --clobber "$f"
done
echo "upload database"
gh release upload "$TAG" -R "$GH_REPO" --clobber "$stage"/glue.*

printf '%s\n' "$remote" | while read -r name _size; do
    [ -n "$name" ] || continue
    case "$name" in glue.db|glue.db.sig|glue.files|glue.files.sig) continue ;; esac
    [ -f "$name" ] && continue
    echo "delete $name"
    gh release delete-asset "$TAG" "$name" -R "$GH_REPO" -y
done
echo "done: https://github.com/$GH_REPO/releases/tag/$TAG"
