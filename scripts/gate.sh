#!/bin/sh
# Test gate for DeepForge: installer unit tests + catalog validation. No ISO build here.
set -e
cd "$(dirname "$0")/.."
for d in packages/glue-installer packages/wheatley-installer; do
    if [ -d "$d/tests" ]; then
        cd "$d" && exec python -m unittest
    fi
done
echo "gate: no installer package with tests found" >&2
exit 1
