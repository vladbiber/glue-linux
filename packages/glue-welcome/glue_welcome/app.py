"""glue-welcome command line."""

from __future__ import annotations

import argparse
import os

from glue_apps.app import data_root, load_config, user_config_path
from glue_apps.config import THEMES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="glue-welcome")
    parser.add_argument("--autostart", action="store_true",
                        help="exit at once when 'Show at startup' is off")
    parser.add_argument("--page", choices=("welcome", "system", "settings"))
    parser.add_argument("--theme", choices=THEMES, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    config = load_config(args.theme)
    if args.autostart and not config.autostart:
        return 0
    if args.page:
        os.environ["GLUE_WELCOME_PAGE"] = args.page
    from .window import run
    return run(config, user_config_path(), data_root())


if __name__ == "__main__":
    raise SystemExit(main())
