"""
Install-run console UI helpers + GeoIP timezone default.

Split from __main__.py (which wires the whole flow) to keep modules small:
the progress bar drawn while execute() runs, the failure log tail, the
final reboot prompt, and the best-effort timezone auto-detection.
"""

from __future__ import annotations

import os
import sys

from glue_installer.disks import DiskError, discover_partitions

_INSTALL_LOG = "/tmp/glue-install.log"

# GeoIP timezone endpoints returning a bare "Area/City" line; tried in order.
_TZ_ENDPOINTS = (
    "https://ipapi.co/timezone",
    "http://ip-api.com/line/?fields=timezone",
)


def report_no_disks(hw, stream=None, wait=None) -> str:
    """Print the storage hint where the disk screen would have been skipped
    silently and wait for Enter on a terminal. `wait` is
    injectable; returns the printed text."""
    from glue_installer.hw_compat import storage_hint
    stream = stream or sys.stderr
    text = storage_hint(False, hw) or ""
    print(f"\n{text}\n", file=stream)
    if wait is None:
        wait = input if sys.stdin.isatty() else None
    if wait is not None:
        try:
            wait("Press Enter to continue... ")
        except EOFError:
            pass
    return text


def detect_timezone(fetch=None) -> "str | None":
    """Best-effort GeoIP timezone ("Europe/Bucharest") for the identity
    default - the wizard still shows and lets the user change it. Returns
    None offline/on bad data; never raises. `fetch` is injectable for tests."""
    from glue_installer.identity import _RE_TIMEZONE

    if fetch is None:
        import urllib.request

        def fetch(url):
            with urllib.request.urlopen(url, timeout=3) as resp:
                return resp.read(256).decode("utf-8", "replace")

    for url in _TZ_ENDPOINTS:
        try:
            tz = fetch(url).strip()
        except Exception:
            continue
        if tz and "/" in tz and _RE_TIMEZONE.match(tz):
            return tz
    return None


def autodetect_spec_timezone(spec, detect=None):
    """Second-chance timezone auto-detection AFTER the wizard: the pre-wizard
    GeoIP lookup usually runs offline (Wi-Fi gets configured inside the
    wizard's network screen), leaving the identity default at "UTC". If the
    final spec still carries that placeholder, retry the lookup now and return
    a copy with the detected local timezone; the spec is returned unchanged
    when it already has a real timezone or detection fails. Never raises."""
    if spec is None or spec.timezone != "UTC":
        return spec
    tz = (detect or detect_timezone)()
    if not tz or tz == "UTC":
        return spec
    import dataclasses
    try:
        return dataclasses.replace(spec, timezone=tz)
    except Exception:
        return spec


# The live ISO has no NTP daemon, so its clock is whatever the RTC said at
# boot - on machines whose RTC holds LOCAL time (BIOS default, Windows dual
# boot) that is hours off. identity_steps later runs `hwclock --systohc`,
# which would write the wrong time back to the RTC and hand the installed
# system a wrong clock on every boot. Threshold below which we leave the
# clock alone (seconds):
_CLOCK_MAX_DRIFT = 120


def sync_clock(fetch_date=None, set_time=None, now=None) -> "int | None":
    """Best-effort: sync the LIVE system clock from an HTTP `Date` response
    header before the install runs. Returns the applied correction in seconds,
    None when unreachable/already accurate. Never raises.

    fetch_date/set_time/now are injectable for tests: fetch_date(url) returns
    the RFC-7231 Date header string, set_time(epoch) applies it, now() returns
    the current epoch.
    """
    import time
    from email.utils import parsedate_to_datetime

    if now is None:
        now = time.time
    if fetch_date is None:
        import urllib.request

        def fetch_date(url):
            with urllib.request.urlopen(url, timeout=3) as resp:
                return resp.headers.get("Date")
    if set_time is None:
        import subprocess

        def set_time(epoch):
            subprocess.run(
                ["date", "-u", "-s", f"@{epoch:.0f}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=True,
            )

    for url in _TZ_ENDPOINTS:
        try:
            header = fetch_date(url)
            if not header:
                continue
            epoch = parsedate_to_datetime(header).timestamp()
        except Exception:
            continue
        drift = epoch - now()
        if abs(drift) <= _CLOCK_MAX_DRIFT:
            return None
        try:
            set_time(epoch)
        except Exception:
            return None
        return int(drift)
    return None


# -- host probes shared by __main__ and the TUI hooks ------------------------

def detect_firmware(efi_dir_exists: bool) -> str:
    """Pure helper: map /sys/firmware/efi presence to a firmware id."""
    return "uefi" if efi_dir_exists else "bios"


# -- TUI I/O hooks (the tui module itself never touches subprocess) ----------

def _net_check() -> str:  # best-effort connectivity check (network screen)
    import subprocess
    try:
        ok = subprocess.run(
            ["ping", "-c", "1", "-W", "2", "artixlinux.org"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    return "connected" if ok else "OFFLINE"


def _open_net_tool() -> None:  # nmtui full-screen (curses suspended by tui)
    import subprocess
    try:
        subprocess.call(["nmtui"])
    except OSError:
        pass  # nmtui missing: the notice already explains the situation


def _repartition(disk_path):  # cfdisk on disk_path, then fresh partition list
    import subprocess
    try:
        subprocess.call(["cfdisk", disk_path])
    except OSError:
        pass
    try:
        return discover_partitions()
    except DiskError:
        return None


def _capture(argv) -> str:
    """stdout of argv; raises OSError when the tool is missing/fails."""
    import subprocess
    r = subprocess.run(list(argv), capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise OSError(f"{argv[0]} exited {r.returncode}")
    return r.stdout


def _firmware(args) -> str:
    if args.firmware == "auto":
        return detect_firmware(os.path.exists("/sys/firmware/efi"))
    return args.firmware


def _step_weight(step) -> int:
    """Rough duration weight per step, so the progress bar tracks wall-clock
    time instead of step count (the single basestrap call dominates)."""
    argv = getattr(step, "argv", None)
    if argv:
        if argv[0] == "basestrap":
            return 60
        if any("limine.conf" in arg or "glue-boot-update" in arg for arg in argv):
            return 5   # chrooted boot.conf writer + glue-boot-update --deploy
        if argv[0].startswith("mkfs.") or argv[0] == "pacman-key":
            return 2
    return 1


def make_progress(steps, out=None):
    """Build an execute() progress callback drawing a single-line bar."""
    if out is None:
        out = sys.stdout
    weights = [_step_weight(s) for s in steps]
    total_weight = sum(weights) or 1

    def callback(index: int, total: int, description: str) -> None:
        done = sum(weights[:index])
        pct = int(done * 100 / total_weight)
        bar = "#" * (pct * 40 // 100)
        out.write(f"\r\033[K[{bar:<40}] {pct:3d}%  {description[:70]}")
        out.flush()

    return callback


def print_log_tail(path: str, lines: int = 30) -> None:
    """Show the end of the install log after a failure (like the old TUI did)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-lines:]
    except OSError:
        return
    if tail:
        print(f"--- last lines of {path} ---", file=sys.stderr)
        sys.stderr.writelines(tail)


def prompt_reboot() -> None:
    """Offer the same 'Reboot now?' finish as the original installer."""
    import subprocess
    try:
        answer = input("Reboot now? (Remove the install media first.) [Y/n] ")
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    if answer.strip().lower() not in ("n", "no"):
        try:
            subprocess.call(["reboot"])
        except OSError:
            print("Could not run 'reboot' — reboot manually when ready.")
