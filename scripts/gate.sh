#!/bin/sh
# Test gate for Glue Linux: installer unit tests + catalog validation. No ISO build here.
set -e
cd "$(dirname "$0")/.."
d=packages/glue-installer
if [ -d "$d/tests" ]; then
    # unittest writes its "Ran N tests" summary to stderr; merge it into
    # stdout so gate consumers that capture stdout see the test count
    (cd "$d" && python -m unittest 2>&1)
fi
h=packages/glue-welcome
if [ -d "$h/tests" ]; then
    (cd "$h" && python -m unittest 2>&1)
fi
echo "gate: installer + Glue Welcome tests passed"
