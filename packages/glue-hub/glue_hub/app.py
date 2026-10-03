"""Command-line entry point; GTK is imported only when the window is needed."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import HubConfig, THEMES, validate_themes


def data_root() -> Path:
    override = os.environ.get("GLUE_HUB_DATA")
    if override:
        return Path(override)
    installed = Path("/usr/share/glue-hub")
    return installed if installed.exists() else Path(__file__).resolve().parent.parent / "data"


def user_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "glue" / "hub.conf"


def self_test() -> int:
    errors = validate_themes(data_root() / "themes")
    for error in errors:
        print(error)
    return int(bool(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="glue-hub")
    parser.add_argument("--autostart", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--theme", choices=THEMES, help=argparse.SUPPRESS)
    parser.add_argument("--page", choices=("home", "apps", "updates", "keys", "system", "settings"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    config = HubConfig.load(user_config_path(), Path("/etc/glue/hub.conf"))
    if args.theme:
        config.theme = args.theme
    if args.page:
        os.environ["GLUE_HUB_START_PAGE"] = args.page
    if args.autostart and not config.autostart:
        return 0
    from .ui import run
    return run(config, user_config_path(), data_root())


if __name__ == "__main__":
    raise SystemExit(main())
