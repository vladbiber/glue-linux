"""CS2 shader warm-up: load each map through Steam until the driver stops compiling pipelines."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import cs2_game

# native build; cs2.exe means a forced Proton, which the warm-up refuses
GAME_COMMS = ("cs2", "cs2.exe")
NATIVE_COMM = "cs2"
# CS2's own compiler threads; DXVK's when D3D11 runs under Proton
PIPELINE_THREADS = ("Async Pipeline", "dxvk-shader")
# casual with bots: an idle player gets auto-joined, dies and spectates the fights
GAME_ARGS = ("-novid", "-nojoy", "+game_type", "0", "+game_mode", "0")
# warm-up writes nothing the player keeps: video/convar files are put back afterwards
USER_CFG_GLOB = "userdata/*/730/local/cfg/*"


@dataclass
class Settle:
    """Async Pipeline CPU in cores; settled once quiet after the map had time to load."""
    min_seconds: float = 60.0
    quiet_seconds: float = 30.0
    quiet_cores: float = 0.25
    busy_cores: float = 1.0
    idle_seconds: float = 120.0
    max_seconds: float = 480.0
    samples: list = field(default_factory=list)
    peak: float = 0.0

    def feed(self, t: float, ticks: int, hz: int = 100) -> str:
        """'wait', 'settled' or 'timeout' after one (seconds since map load, cpu ticks) sample."""
        self.samples.append((t, ticks))
        rate = self.rate(t - 4, hz)
        self.peak = max(self.peak, rate)
        if t >= self.max_seconds:
            return "timeout"
        if t < self.min_seconds or self.samples[0][0] > t - self.quiet_seconds:
            return "wait"
        if self.rate(t - self.quiet_seconds, hz) >= self.quiet_cores:
            return "wait"
        # never busy: either the cache is already warm or loading has not finished yet
        if self.peak < self.busy_cores and t < self.idle_seconds:
            return "wait"
        return "settled"

    def rate(self, since: float, hz: int = 100) -> float:
        t1, c1 = self.samples[-1]
        older = [s for s in self.samples if s[0] <= since] or self.samples[:1]
        t0, c0 = older[-1]
        return (c1 - c0) / hz / (t1 - t0) if t1 > t0 else 0.0


def _thread_stats(task_dir: Path) -> tuple[str, int] | None:
    try:
        raw = (task_dir / "stat").read_text()
    except OSError:
        return None
    lpar, rpar = raw.find("("), raw.rfind(")")
    fields = raw[rpar + 2:].split()
    try:
        return raw[lpar + 1:rpar], int(fields[11]) + int(fields[12])
    except (IndexError, ValueError):
        return None


def pipeline_ticks(pid: int, proc: Path = Path("/proc")) -> int | None:
    """utime+stime of the game's pipeline compiler threads; None once the process is gone."""
    try:
        tasks = list((proc / str(pid) / "task").iterdir())
    except OSError:
        return None
    total = 0
    for task in tasks:
        stats = _thread_stats(task)
        if stats and stats[0].startswith(PIPELINE_THREADS):
            total += stats[1]
    return total


def game_pids(proc: Path = Path("/proc"), uid: int | None = None) -> set[int]:
    uid = os.getuid() if uid is None else uid
    pids = set()
    for entry in proc.iterdir() if proc.is_dir() else ():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid == uid and (entry / "comm").read_text().strip() in GAME_COMMS:
                pids.add(int(entry.name))
        except OSError:
            continue
    return pids


def steam_launch_alive(proc: Path = Path("/proc"), uid: int | None = None) -> bool:
    """Steam's reaper for app 730 lives until Steam has registered the game as stopped."""
    uid = os.getuid() if uid is None else uid
    want = f"AppId={cs2_game.APPID}".encode()
    for entry in proc.iterdir() if proc.is_dir() else ():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            args = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if b"SteamLaunch" in args and want in args:
            return True
    return False


def map_open(pid: int, name: str, proc: Path = Path("/proc")) -> bool:
    """The game holds maps/<name>.vpk open once the level starts loading."""
    want = f"/maps/{name}.vpk"
    try:
        fds = list((proc / str(pid) / "fd").iterdir())
    except OSError:
        return False
    for fd in fds:
        try:
            if os.readlink(fd).endswith(want):
                return True
        except OSError:
            continue
    return False


def steam_command(root: Path, which=shutil.which) -> list[str] | None:
    if ".var/app/com.valvesoftware.Steam" in str(root):
        return ["flatpak", "run", "com.valvesoftware.Steam"] if which("flatpak") else None
    steam = which("steam")
    return [steam] if steam else None


def launch_argv(steam: list[str], name: str) -> list[str]:
    if not cs2_game.MAP_RE.match(name):
        raise ValueError(f"bad map name: {name!r}")
    return [*steam, "-applaunch", cs2_game.APPID, *GAME_ARGS, "+map", name]


class Cancelled(Exception):
    pass


class WarmupError(Exception):
    pass


class Runner:
    """Runs the warm-up map by map; every effect is injectable for tests."""

    def __init__(self, steam_root: Path, game: Path, driver: str, maps: list[str],
                 state: cs2_game.State, steam: list[str], *, proc: Path = Path("/proc"),
                 spawn: Callable = None, kill: Callable = os.kill,
                 clock: Callable = time.monotonic, cancel: threading.Event | None = None,
                 settle: Callable[[], Settle] = Settle, poll: float = 2.0,
                 start_timeout: float = 300.0, load_timeout: float = 240.0,
                 stop_timeout: float = 60.0):
        self.root, self.game, self.driver, self.maps = steam_root, game, driver, maps
        self.state, self.steam, self.proc = state, steam, proc
        self.spawn = spawn or (lambda argv: subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True))
        self.kill, self.clock = kill, clock
        self.cancel = cancel or threading.Event()
        self.new_settle, self.poll = settle, poll
        self.start_timeout, self.load_timeout = start_timeout, load_timeout
        self.stop_timeout = stop_timeout
        self.hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def wait(self, seconds: float) -> None:
        if self.cancel.wait(seconds):
            raise Cancelled

    def run(self, report: Callable[..., None]) -> None:
        if game_pids(self.proc) or steam_launch_alive(self.proc):
            raise WarmupError("cs2-running")
        # offline +map only: never let the user's launch options join a server
        if cs2_game.joins_server(cs2_game.launch_options(self.root)):
            raise WarmupError("launch-options")
        # a Proton build has other caches and shows a black screen here
        if cs2_game.compat_tool(self.root):
            raise WarmupError("proton")
        saved = self.snapshot()
        try:
            for index, name in enumerate(self.maps):
                report("map", index=index, total=len(self.maps), name=name)
                info = self.one_map(name, report)
                self.state.mark(self.driver, cs2_game.build_id(self.game), name, info)
            self.state.finish(self.driver)
            report("finished", total=len(self.maps))
        finally:
            self.restore(saved)

    def one_map(self, name: str, report: Callable[..., None]) -> dict:
        before = game_pids(self.proc)
        self.spawn(launch_argv(self.steam, name))
        pid = self.until(lambda: next(iter(game_pids(self.proc) - before), None),
                         self.start_timeout, "no-start")
        try:
            if not self.native(pid):
                raise WarmupError("proton")
            report("loading", name=name)
            self.until(lambda: map_open(pid, name, self.proc) or self.gone(pid),
                       self.load_timeout, "no-load")
            if self.gone(pid):
                raise WarmupError("exited")
            settle, start = self.new_settle(), self.clock()
            while True:
                ticks = pipeline_ticks(pid, self.proc)
                if ticks is None:
                    raise WarmupError("exited")
                elapsed = self.clock() - start
                verdict = settle.feed(elapsed, ticks, self.hz)
                report("compiling", name=name, seconds=elapsed,
                       cores=settle.rate(elapsed - 4, self.hz))
                if verdict != "wait":
                    return {"seconds": round(elapsed), "peak_cores": round(settle.peak, 1),
                            "result": verdict}
                self.wait(self.poll)
        finally:
            self.stop(pid)

    def until(self, probe: Callable, timeout: float, error: str):
        end = self.clock() + timeout
        while True:
            value = probe()
            if value:
                return value
            if self.clock() >= end:
                raise WarmupError(error)
            self.wait(self.poll)

    def native(self, pid: int) -> bool:
        try:
            return (self.proc / str(pid) / "comm").read_text().strip() == NATIVE_COMM
        except OSError:
            return True

    def gone(self, pid: int) -> bool:
        return not (self.proc / str(pid)).exists()

    def stop(self, pid: int) -> None:
        """SIGTERM like Steam's Stop button, SIGKILL if it hangs; then let Steam notice."""
        for sig, grace in ((signal.SIGTERM, 30.0), (signal.SIGKILL, 10.0)):
            try:
                self.kill(pid, sig)
            except ProcessLookupError:
                break
            end = self.clock() + grace
            while not self.gone(pid) and self.clock() < end:
                time.sleep(min(self.poll, 0.5))
            if self.gone(pid):
                break
        self.until(lambda: not steam_launch_alive(self.proc), self.stop_timeout, "steam-busy")

    def snapshot(self) -> dict[Path, bytes]:
        saved = {}
        for path in self.root.glob(USER_CFG_GLOB):
            try:
                if path.is_file() and path.stat().st_size < 4 << 20:
                    saved[path] = path.read_bytes()
            except OSError:
                continue
        return saved

    def restore(self, saved: dict[Path, bytes]) -> None:
        for path, data in saved.items():
            try:
                if path.read_bytes() != data:
                    path.write_bytes(data)
            except OSError:
                continue
