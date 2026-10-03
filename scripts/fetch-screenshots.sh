#!/bin/sh
# Fetch the desktop-environment screenshots listed in catalog/screenshots/SOURCES.tsv
# (official/Commons sources, free licences, see CREDITS.md) and convert them to
# 1920x1080 PNG with a palette-coloured letterbox.
#   sh scripts/fetch-screenshots.sh           download what is missing/changed
#   sh scripts/fetch-screenshots.sh --check   only verify the cached originals' hashes
#                                             (a missing cache is reported, not an error)
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
DIR=$ROOT/packages/glue-installer/catalog/screenshots
MANIFEST=$DIR/SOURCES.tsv
CACHE=$DIR/.cache
PALETTE=$ROOT/packages/glue-branding/palette.json
UA='glue-linux-fetch/1.0 (https://github.com/vladbiber/glue-linux)'

check_only=0
case "${1:-}" in
    "") ;;
    --check) check_only=1 ;;
    *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

[ -f "$MANIFEST" ] || { echo "missing $MANIFEST" >&2; exit 1; }
bg=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bg"])' "$PALETTE")
case "$bg" in '#'??????) ;; *) echo "bad bg colour in $PALETTE: $bg" >&2; exit 1 ;; esac

mkdir -p "$CACHE"
tab=$(printf '\t')
tail -n +2 "$MANIFEST" | while IFS="$tab" read -r file url sha author license page; do
    [ -n "$file" ] || continue
    orig=$CACHE/$file.orig
    have=
    [ -f "$orig" ] && have=$(sha256sum "$orig" | cut -d' ' -f1)
    if [ "$check_only" = 1 ]; then
        if [ ! -f "$DIR/$file" ]; then
            echo "FAIL    $file: not on disk" >&2
            exit 1
        elif [ -z "$have" ]; then
            echo "nocache $file (no cached original to verify; run without --check)"
        elif [ "$have" = "$sha" ]; then
            echo "ok      $file"
        else
            echo "FAIL    $file: cached original has sha256 $have, manifest says $sha" >&2
            exit 1
        fi
        continue
    fi
    if [ -f "$DIR/$file" ] && [ "$have" = "$sha" ]; then
        echo "skip    $file (up to date)"
        continue
    fi
    echo "fetch   $file <- $url"
    curl -fsSL -A "$UA" -o "$orig.part" "$url"
    got=$(sha256sum "$orig.part" | cut -d' ' -f1)
    if [ "$got" != "$sha" ]; then
        rm -f "$orig.part"
        echo "FAIL    $file: sha256 mismatch, got $got, manifest says $sha" >&2
        exit 1
    fi
    mv "$orig.part" "$orig"
    magick "$orig" -resize 1920x1080 -background "$bg" -gravity center \
        -extent 1920x1080 -strip -define png:compression-level=9 "png:$DIR/$file"
    echo "wrote   $file"
done
