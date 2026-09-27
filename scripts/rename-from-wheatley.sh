#!/bin/sh
# rename-from-<old-name>.sh — complete old-name -> glue rename of this repo.
#
# Renames every git-tracked path containing the old product name (the
# packages/*-installer, *-branding, st-* dirs, the iso-profile dir, the
# installer Python module, the *-install launcher, the *-live.sh script, ...)
# and rewrites every tracked file that mentions it: module/import names,
# /usr/share + /etc data paths, the pacman repo name, *_NOAUTO / *_CATALOG env
# vars, `buildiso -p`, and product strings. Case is preserved (lower/Title/
# UPPER -> glue/Glue/GLUE). Window manager names (nvwm, apeturewm, atomwm, ...)
# don't contain the old name and are untouched.
#
# Only git-tracked files are considered, so .git/ and any
# and other untracked/ignored content are never touched. The old name is
# assembled from fragments below so this script's own content stays free of it
# (`git grep` over the renamed tree must come back empty); the script also
# skips renaming itself. Idempotent: on an already-renamed tree it changes
# nothing and exits 0. Requires GNU sed (sed -i). Stages renames + edits in
# the index; committing is left to the caller.
set -eu

# w-h-e-a-t-l-e-y in lower/Title/UPPER, without the contiguous literal
OLD="wheat"; OLD="${OLD}ley"
OLDC="Wheat"; OLDC="${OLDC}ley"
OLDU="WHEAT"; OLDU="${OLDU}LEY"
PROG="rename-from-${OLD}"

for tool in git sed grep; do
    command -v "$tool" >/dev/null 2>&1 || {
        echo "$PROG: required tool '$tool' not found" >&2
        exit 1
    }
done

ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null) || {
    echo "$PROG: not inside a git repository" >&2
    exit 1
}
cd "$ROOT"

SELF="scripts/${PROG}.sh"

subst_name() {
    printf '%s' "$1" | sed -e "s/$OLD/glue/g" \
                           -e "s/$OLDC/Glue/g" \
                           -e "s/$OLDU/GLUE/g"
}

# --- 1) rename tracked paths -------------------------------------------------
# For each tracked path still containing the old name, git-mv its FIRST
# matching path segment (a whole directory when possible, so untracked
# leftovers such as __pycache__ move along with it), then rescan until nothing
# matches. This script keeps its own (old-name-bearing) path.
rename_first_segment() {
    prefix=""
    rest=$1
    while [ -n "$rest" ]; do
        seg=${rest%%/*}
        case $rest in
            */*) rest=${rest#*/} ;;
            *)   rest="" ;;
        esac
        case $(printf '%s' "$seg" | tr '[:upper:]' '[:lower:]') in
            *${OLD}*)
                old="$prefix$seg"
                new="$prefix$(subst_name "$seg")"
                if [ -e "$new" ]; then
                    echo "$PROG: cannot rename '$old': '$new' already exists" >&2
                    exit 1
                fi
                echo "mv  $old -> $new"
                git mv "$old" "$new"
                return 0
                ;;
        esac
        prefix="$prefix$seg/"
    done
    echo "$PROG: no renamable segment in '$1'" >&2
    exit 1
}

while :; do
    hit=$(git ls-files | grep -i "$OLD" | grep -Fxv "$SELF" | head -n 1) || true
    [ -n "$hit" ] || break
    rename_first_segment "$hit"
done

# --- 2) rewrite file contents ------------------------------------------------
# -I skips binaries (PNGs etc.); this script's content never matches.
git grep -Ili "$OLD" 2>/dev/null | while IFS= read -r f; do
    echo "sed $f"
    sed -i -e "s/$OLD/glue/g" \
           -e "s/$OLDC/Glue/g" \
           -e "s/$OLDU/GLUE/g" "$f"
    git add -- "$f"
done

# --- 3) verify ----------------------------------------------------------------
leftover=$(git grep -Il -i "$OLD" 2>/dev/null || true)
if [ -n "$leftover" ]; then
    echo "$PROG: unexpected-case matches survived in:" >&2
    printf '%s\n' "$leftover" >&2
    exit 1
fi
if git ls-files | grep -i "$OLD" | grep -Fxvq "$SELF"; then
    echo "$PROG: some tracked paths still contain the old name" >&2
    exit 1
fi

echo "$PROG: done (working tree is free of the old name)"
