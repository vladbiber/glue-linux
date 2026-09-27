#!/bin/sh
# Test gate for Glue Linux: installer unit tests + catalog validation. No ISO build here.
set -e
cd "$(dirname "$0")/.."
d=packages/glue-installer
if [ -d "$d/tests" ]; then
    cd "$d" && exec python -m unittest
fi
echo "gate: no installer package with tests found" >&2
exit 1
