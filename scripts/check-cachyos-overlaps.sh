#!/bin/bash
# check-cachyos-overlaps.sh — Audit [cachyos] vs Artix repo package overlaps.
#
# Downloads real pacman DB files, finds packages whose NAME appears in both
# [cachyos] and at least one Artix repo (system/world/galaxy/lib32), then for
# each overlap checks whether the [cachyos] variant lists a systemd* package
# in its %DEPENDS% section.
#
# Output: one line per overlap — name, versions, CLEAN or SYSTEMD marker.
# Exit 0: no SYSTEMD overlaps.
# Exit 1: at least one SYSTEMD overlap found (use as a guardian in CI).
#
# Requirements: bash, curl, tar, awk, sort (POSIX utilities + curl).

set -euo pipefail

CACHYOS_URL="https://mirror.cachyos.org/repo/x86_64/cachyos/cachyos.db"
ARTIX_BASE="https://mirror1.artixlinux.org/repos"
ARTIX_REPOS="system world galaxy lib32"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------
# fetch_db <url> <destdir>  — download and extract a pacman .db tarball
# Downloads to a temp file first to avoid SIGPIPE from tar closing early.
# ---------------------------------------------------------------------------
fetch_db() {
    local url="$1" dest="$2"
    local tmpfile
    tmpfile=$(mktemp "$TMP/db_XXXXXX")
    mkdir -p "$dest"
    curl -fsSL --retry 3 --retry-delay 2 -o "$tmpfile" "$url"
    tar -C "$dest" -xf "$tmpfile" 2>/dev/null
    rm -f "$tmpfile"
}

# ---------------------------------------------------------------------------
# get_field <desc_file> <FIELDNAME>  — print all lines under %FIELDNAME%
# Stops at the next %SECTION% or blank line (pacman DB format).
# ---------------------------------------------------------------------------
get_field() {
    awk -v tag="%${2}%" '
        $0 == tag { in_section=1; next }
        in_section && /^%/ { exit }
        in_section && /^[[:space:]]*$/ { exit }
        in_section { print }
    ' "$1"
}

# ---------------------------------------------------------------------------
# Fetch repos
# ---------------------------------------------------------------------------
echo "Fetching [cachyos] DB..." >&2
fetch_db "$CACHYOS_URL" "$TMP/cachyos"

for repo in $ARTIX_REPOS; do
    echo "Fetching [$repo] DB..." >&2
    fetch_db "${ARTIX_BASE}/${repo}/os/x86_64/${repo}.db" "$TMP/artix_${repo}"
done

# ---------------------------------------------------------------------------
# Build lookup table: Artix package name -> "repo version"
# Uses a temp file (name<TAB>repo<TAB>version) for shell-portable lookup.
# ---------------------------------------------------------------------------
artix_index="$TMP/artix_index.tsv"
: > "$artix_index"

for repo in $ARTIX_REPOS; do
    dir="$TMP/artix_${repo}"
    [ -d "$dir" ] || continue
    for d in "$dir"/*/; do
        [ -f "$d/desc" ] || continue
        name=$(get_field "$d/desc" NAME)
        version=$(get_field "$d/desc" VERSION)
        [ -n "$name" ] || continue
        printf '%s\t%s\t%s\n' "$name" "$repo" "$version" >> "$artix_index"
    done
done

# Sort the index for deduplication (keep first occurrence per name = Artix prio order)
sort -t$'\t' -k1,1 -u -o "$artix_index" "$artix_index"

# ---------------------------------------------------------------------------
# Scan [cachyos] for overlaps
# ---------------------------------------------------------------------------
total=0
systemd_count=0
results="$TMP/results.txt"
: > "$results"

for d in "$TMP/cachyos"/*/; do
    [ -f "$d/desc" ] || continue
    name=$(get_field "$d/desc" NAME)
    [ -n "$name" ] || continue

    # Check if name exists in Artix index
    artix_row=$(awk -F'\t' -v n="$name" '$1==n{print; exit}' "$artix_index")
    [ -n "$artix_row" ] || continue

    artix_repo=$(printf '%s' "$artix_row" | cut -f2)
    artix_ver=$(printf '%s' "$artix_row" | cut -f3)
    cachyos_ver=$(get_field "$d/desc" VERSION)

    # Check %DEPENDS% for systemd* (hard deps only, not optdeps)
    if get_field "$d/desc" DEPENDS | grep -qE '^systemd'; then
        marker="SYSTEMD"
        systemd_count=$((systemd_count + 1))
    else
        marker="CLEAN"
    fi

    printf '%-40s  cachyos=%-28s  artix[%s]=%-20s  %s\n' \
        "$name" "$cachyos_ver" "$artix_repo" "$artix_ver" "$marker" >> "$results"
    total=$((total + 1))
done

# ---------------------------------------------------------------------------
# Output: sorted by name
# ---------------------------------------------------------------------------
sort "$results"

echo "" >&2
echo "Total overlaps [cachyos] ∩ (system|world|galaxy|lib32): $total" >&2
if [ "$systemd_count" -eq 0 ]; then
    echo "RESULT: No SYSTEMD dependencies in overlapping packages. [cachyos] can safely go first." >&2
else
    echo "RESULT: $systemd_count package(s) with SYSTEMD deps found — keep Artix repos first." >&2
fi

[ "$systemd_count" -eq 0 ]
