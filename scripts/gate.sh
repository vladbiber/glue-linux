#!/bin/sh
# Test gate for Glue Linux: installer unit tests + catalog validation. No ISO build here.
set -e
cd "$(dirname "$0")/.."
d=packages/glue-installer
if [ -d "$d/tests" ]; then
    # unittest writes its "Ran N tests" summary to stderr; merge it into
    # stdout so gate consumers that capture stdout see the test count
    cd "$d" && exec python -m unittest 2>&1
fi
echo "gate: no installer package with tests found" >&2
exit 1
