#!/bin/sh
# Run Glue Welcome from the checkout. Needs a glue-apps checkout next to this
# repository (or GLUE_APPS_SRC pointing at one).
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
apps=${GLUE_APPS_SRC:-$root/../glue-apps}
python_bin=${PYTHON:-python3}

if [ ! -d "$apps/glue_apps" ]; then
    echo "glue-apps not found at $apps; clone https://github.com/vladbiber/glue-apps there" >&2
    exit 1
fi

export GLUE_APPS_DATA="$apps/data"
export PYTHONPATH="$root/packages/glue-welcome:$apps${PYTHONPATH:+:$PYTHONPATH}"
exec "$python_bin" -m glue_welcome.app "$@"
