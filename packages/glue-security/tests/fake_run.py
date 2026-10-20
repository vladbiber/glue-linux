"""Stand-in for every command glue-security runs (GLUE_SECURITY_RUN).

Logs argv to $FAKE_LOG; `artix-chroot ROOT pacman -S ...` records the
packages in ROOT's local db; `usbguard generate-policy` prints $FAKE_USB.
"""
import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(argv) + "\n")
if os.environ.get("FAKE_FAIL") and os.environ["FAKE_FAIL"] in argv:
    sys.exit(1)
root = Path(argv[1]) if argv[:1] == ["artix-chroot"] else None
rest = argv[2:] if root else argv
if rest[:1] == ["pacman"] and root:
    for name in rest:
        if not name.startswith("-") and name != "pacman":
            d = root / "var/lib/pacman/local" / f"{name}-1.0-1"
            d.mkdir(parents=True, exist_ok=True)
            (d / "desc").write_text(f"%NAME%\n{name}\n")
elif rest[-1:] == ["generate-policy"]:
    sys.stdout.write(os.environ.get("FAKE_USB", ""))
