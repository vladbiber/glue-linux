#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
hub="$root/packages/glue-hub"
python_bin=${PYTHON:-python3}

if ! "$python_bin" -c \
    'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1"); from gi.repository import Gtk, Adw' \
    >/dev/null 2>&1; then
    echo "Lipsesc Python GObject, GTK4 sau libadwaita." >&2
    echo "Pe Gentoo instalează dev-python/pygobject, gui-libs/gtk și gui-libs/libadwaita." >&2
    exit 1
fi

export GLUE_HUB_DATA="$hub/data"
export PYTHONPATH="$hub${PYTHONPATH:+:$PYTHONPATH}"
exec "$python_bin" -m glue_hub.app "$@"
