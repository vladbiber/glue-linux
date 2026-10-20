import json
import os
import shutil
import signal
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from glue_welcome import cs2_game, cs2_warmup, cs2_warmup_window
from glue_welcome.strings import RO

GAMEMODES = """// DO NOT MODIFY
"GameModes.txt"
{
	"mapgroups"
	{
		"mg_active"
		{
			"name"	"mg_active"   // trailing comment
			"maps"
			{
				"de_mirage"	""
				"de_gone"	""
				"de_dust2"	""
			}
		}
	}
}
"""


def make_vpk(path: Path, files: dict[str, tuple[bytes, bool]]) -> None:
    """VPK v2 set: name -> (data, in_dir_file); others go to pak01_000.vpk with 2 preload bytes."""
    tree, inline, archive = b"", b"", b""
    for name, (data, in_dir) in files.items():
        folder, _, base = name.rpartition("/")
        stem, _, ext = base.rpartition(".")
        if in_dir or len(data) < 2:
            entry = struct.pack("<IHHIIH", 0, 0, 0x7FFF, len(inline), len(data), 0xFFFF)
            inline += data
        else:
            entry = struct.pack("<IHHIIH", 0, 2, 0, len(archive), len(data) - 2, 0xFFFF) + data[:2]
            archive += data[2:]
        tree += ext.encode() + b"\0" + (folder or " ").encode() + b"\0" \
            + stem.encode() + b"\0" + entry + b"\0\0"
    tree += b"\0"
    head = struct.pack("<IIIIIII", 0x55AA1234, 2, len(tree), len(inline), 0, 0, 0)
    path.write_bytes(head + tree + inline)
    path.with_name("pak01_000.vpk").write_bytes(archive)


def make_game(home: Path, maps: list[str], gamemodes: str = GAMEMODES,
              library: Path | None = None) -> Path:
    steam = home / ".local/share/Steam"
    (steam / "steamapps").mkdir(parents=True)
    lib = library or steam
    if library:
        (library / "steamapps").mkdir(parents=True)
        (steam / "steamapps/libraryfolders.vdf").write_text(
            f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{steam}"\n\t}}\n'
            f'\t"1"\n\t{{\n\t\t"path"\t\t"{library}"\n\t}}\n}}\n')
    (lib / "steamapps/appmanifest_730.acf").write_text('"AppState"\n{\n\t"buildid"\t\t"123"\n}\n')
    game = lib / cs2_game.GAME_DIR
    (game / "csgo/maps").mkdir(parents=True)
    for name in maps:
        (game / "csgo/maps" / f"{name}.vpk").write_bytes(b"")
    make_vpk(game / "csgo/pak01_dir.vpk", {"gamemodes.txt": (gamemodes.encode(), False)})
    return game


class TestGameFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_kv(self):
        kv = cs2_game.parse_kv(GAMEMODES)
        group = kv["GameModes.txt"]["mapgroups"]["mg_active"]
        self.assertEqual(group["name"], "mg_active")
        self.assertEqual(list(group["maps"]), ["de_mirage", "de_gone", "de_dust2"])
        self.assertEqual(group["maps"]["de_dust2"], "")

    def test_vpk_dir_and_archive_entries(self):
        vpk = self.home / "pak01_dir.vpk"
        make_vpk(vpk, {"scripts/a.txt": (b"inline data", True), "b.cfg": (b"archived!", False)})
        self.assertEqual(cs2_game.vpk_file(vpk, "scripts/a.txt"), b"inline data")
        self.assertEqual(cs2_game.vpk_file(vpk, "b.cfg"), b"archived!")
        self.assertIsNone(cs2_game.vpk_file(vpk, "missing.txt"))
        (self.home / "bad_dir.vpk").write_bytes(b"\0" * 40)
        self.assertIsNone(cs2_game.vpk_file(self.home / "bad_dir.vpk", "b.cfg"))
        self.assertIsNone(cs2_game.vpk_file(self.home / "none_dir.vpk", "b.cfg"))

    def test_map_order(self):
        game = make_game(self.home, ["de_dust2", "de_mirage", "de_ancient", "de_ancient_vanity",
                                     "de_mirage_vanity", "de_boulder", "cs_office",
                                     "graphics_settings", "lobby_mapveto", "Bad-Name"])
        self.assertEqual(cs2_game.active_group(game), ["de_mirage", "de_gone", "de_dust2"])
        self.assertEqual(cs2_game.warmup_maps(game), ["de_mirage", "de_dust2", "de_ancient"])
        self.assertEqual(cs2_game.warmup_maps(game, everything=True),
                         ["de_mirage", "de_dust2", "de_ancient", "cs_office", "de_boulder"])

    def test_missing_gamemodes_falls_back_to_vanity_maps(self):
        game = make_game(self.home, ["de_nuke", "de_nuke_vanity", "de_dust2"], gamemodes="")
        self.assertEqual(cs2_game.warmup_maps(game), ["de_nuke"])

    def test_find_cs2_in_second_library(self):
        lib = self.home / "games"
        game = make_game(self.home, ["de_dust2"], library=lib)
        root, found = cs2_game.find_cs2(self.home)
        self.assertEqual(found, game)
        self.assertEqual(root, self.home / ".local/share/Steam")
        self.assertEqual(cs2_game.build_id(game), "123")

    def test_no_cs2(self):
        (self.home / ".local/share/Steam/steamapps").mkdir(parents=True)
        self.assertIsNone(cs2_game.find_cs2(self.home))

    def test_nvidia_driver(self):
        self.assertEqual(cs2_game.nvidia_driver(self.home), "")
        ver = self.home / "sys/module/nvidia/version"
        ver.parent.mkdir(parents=True)
        ver.write_text("615.78.08\n")
        self.assertEqual(cs2_game.nvidia_driver(self.home), "615.78.08")

    def test_state_follows_driver(self):
        state = cs2_game.State(self.home / cs2_game.STATE_FILE)
        state.mark("1.0", "9", "de_dust2", {"seconds": 80})
        state.finish("1.0")
        again = cs2_game.State(self.home / cs2_game.STATE_FILE)
        self.assertEqual(again.maps_done("1.0"), {"de_dust2"})
        self.assertTrue(again.complete("1.0"))
        self.assertFalse(again.complete("2.0"))
        self.assertEqual(again.maps_done("2.0"), set())
        again.decline()
        again.mark("2.0", "9", "de_nuke", {})
        self.assertTrue(again.declined)
        self.assertEqual(again.maps_done("2.0"), {"de_nuke"})
        self.assertFalse(again.complete("2.0"))

    def test_launch_options_that_join_a_server(self):
        steam = self.home / "Steam"
        cfg = steam / "userdata/5/config/localconfig.vdf"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('"UserLocalConfigStore"\n{\n"Software"{"Valve"{"Steam"{"apps"{\n'
                       '"730"{"LaunchOptions"\t"mangohud %command% -novid"}\n'
                       '"570"{"LaunchOptions"\t"+connect 1.2.3.4"}}}}}\n}\n')
        self.assertEqual(cs2_game.launch_options(steam), ["mangohud %command% -novid"])
        self.assertFalse(cs2_game.joins_server(["mangohud %command% -novid +fps_max 0"]))
        for bad in ("+connect 1.2.3.4", "%command% +CONNECT x", "-connect x",
                    "+playdemo a", "steam://connect/1.2.3.4"):
            self.assertTrue(cs2_game.joins_server([bad]), bad)
        self.assertFalse(cs2_game.joins_server(["+cl_connect_hint 1"]))

    def test_compat_tool_mapping(self):
        steam = self.home / "Steam"
        (steam / "config").mkdir(parents=True)
        cfg = steam / "config/config.vdf"
        cfg.write_text('"InstallConfigStore"{"Software"{"Valve"{"Steam"{"CompatToolMapping"{'
                       '"1017180"{"name" "proton-cachyos-slr" "priority" "250"}}}}}}')
        self.assertEqual(cs2_game.compat_tool(steam), "")
        cfg.write_text('"InstallConfigStore"{"Software"{"Valve"{"Steam"{"CompatToolMapping"{'
                       '"730"{"name" "GE-Proton11-1" "config" "" "priority" "250"}}}}}}')
        self.assertEqual(cs2_game.compat_tool(steam), "GE-Proton11-1")
        cfg.write_text('"InstallConfigStore"{"Software"{"Valve"{"Steam"{"CompatToolMapping"{'
                       '"730"{"name" "" "priority" "250"}}}}}}')
        self.assertEqual(cs2_game.compat_tool(steam), "")
        local = steam / "userdata/5/config/localconfig.vdf"
        local.parent.mkdir(parents=True)
        local.write_text('"UserLocalConfigStore"{"CompatToolMapping"{"730"{"name" "proton_9"}}}')
        self.assertEqual(cs2_game.compat_tool(steam), "proton_9")

    def test_corrupt_state_is_empty(self):
        path = self.home / cs2_game.STATE_FILE
        path.parent.mkdir(parents=True)
        path.write_text("[1, 2")
        self.assertEqual(cs2_game.State(path).maps_done("1"), set())

    def test_pending_and_needs_warmup(self):
        game = make_game(self.home, ["de_dust2", "de_mirage"])
        self.assertIsNone(cs2_game.pending(self.home, self.home))
        self.assertFalse(cs2_game.needs_warmup(self.home, self.home))
        ver = self.home / "sys/module/nvidia/version"
        ver.parent.mkdir(parents=True)
        ver.write_text("615.78.08\n")
        self.assertTrue(cs2_game.needs_warmup(self.home, self.home))
        root, found, driver, left = cs2_game.pending(self.home, self.home)
        self.assertEqual((found, driver, left), (game, "615.78.08", ["de_mirage", "de_dust2"]))
        state = cs2_game.State(self.home / cs2_game.STATE_FILE)
        state.mark(driver, "123", "de_mirage", {})
        self.assertEqual(cs2_game.pending(self.home, self.home)[3], ["de_dust2"])
        state.mark(driver, "123", "de_dust2", {})
        state.finish(driver)
        self.assertIsNone(cs2_game.pending(self.home, self.home))
        self.assertFalse(cs2_game.needs_warmup(self.home, self.home))


class TestSettle(unittest.TestCase):
    def run_curve(self, cores, settle=None, step=2.0):
        settle = settle or cs2_warmup.Settle()
        t, ticks = 0.0, 0
        while True:
            verdict = settle.feed(t, int(ticks))
            if verdict != "wait":
                return verdict, t
            ticks += cores(t) * 100 * step
            t += step

    def test_settles_after_compiling_stops(self):
        verdict, t = self.run_curve(lambda t: 5.0 if 10 <= t < 150 else 0.02)
        self.assertEqual(verdict, "settled")
        self.assertTrue(180 <= t <= 190, t)

    def test_waits_for_min_seconds(self):
        verdict, t = self.run_curve(lambda t: 4.0 if t < 8 else 0.0)
        self.assertEqual((verdict, t), ("settled", 60.0))

    def test_never_busy_waits_idle_seconds(self):
        verdict, t = self.run_curve(lambda t: 0.1)
        self.assertEqual((verdict, t), ("settled", 120.0))

    def test_short_pause_does_not_settle(self):
        busy = lambda t: 0.0 if 70 <= t < 90 else 3.0
        verdict, t = self.run_curve(busy, cs2_warmup.Settle(max_seconds=200))
        self.assertEqual((verdict, t), ("timeout", 200.0))


def write_task(task: Path, comm: str, ticks: int) -> None:
    task.mkdir(parents=True, exist_ok=True)
    (task / "stat").write_text(f"{task.name} ({comm}) S 1 1 1 0 -1 0 0 0 0 0 {ticks} 0 0 0 20 0\n")


class FakeGame:
    """A cs2 process in a fake /proc whose pipeline threads follow cores(t)."""

    def __init__(self, proc: Path, clock, cores=lambda t: 0.0, pid=4242):
        self.proc, self.clock, self.cores, self.pid = proc, clock, cores, pid
        self.launched, self.killed = [], []
        self.ticks, self.last = 0.0, None
        self.reaper_polls, self.reaper_left = 2, 0

    @property
    def dir(self) -> Path:
        return self.proc / str(self.pid)

    def spawn(self, argv):
        self.launched.append(argv)
        self.pid += 1
        self.dir.mkdir(parents=True)
        (self.dir / "comm").write_text("cs2\n")
        write_task(self.dir / "task" / "1", "cs2", 50)
        (self.dir / "fd").mkdir()
        os.symlink(f"/games/csgo/maps/{argv[-1]}.vpk", self.dir / "fd" / "7")
        reaper = self.proc / "100"
        reaper.mkdir(exist_ok=True)
        (reaper / "cmdline").write_bytes(b"reaper\0SteamLaunch\0AppId=730\0--\0cs2\0")
        self.reaper_left = self.reaper_polls
        self.ticks, self.last = 0.0, None

    def tick(self, now):
        # Steam's reaper exits a few polls after the game
        if not self.dir.exists() and (self.proc / "100").exists():
            self.reaper_left -= 1
            if self.reaper_left < 0:
                shutil.rmtree(self.proc / "100")
        if self.dir.exists():
            if self.last is not None:
                self.ticks += self.cores(now) * 100 * (now - self.last)
            self.last = now
            write_task(self.dir / "task" / "2", "Async Pipeline ", int(self.ticks))
            write_task(self.dir / "task" / "3", "Async Pipeline ", 0)

    def kill(self, pid, sig):
        self.killed.append((pid, sig))
        if not (self.proc / str(pid)).exists():
            raise ProcessLookupError(pid)
        shutil.rmtree(self.proc / str(pid))


class FakeClock:
    def __init__(self, step=2.0):
        self.now, self.step, self.hooks = 0.0, step, []

    def __call__(self):
        self.now += self.step
        for hook in self.hooks:
            hook(self.now)
        return self.now


class TestProc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pipeline_ticks_and_odd_names(self):
        write_task(self.proc / "9/task/1", "cs2", 500)
        write_task(self.proc / "9/task/2", "Async Pipeline ", 120)
        write_task(self.proc / "9/task/3", "Async Pipeline ", 30)
        write_task(self.proc / "9/task/4", "a) (b", 999)
        self.assertEqual(cs2_warmup.pipeline_ticks(9, self.proc), 150)
        write_task(self.proc / "9/task/5", "dxvk-shader-2", 7)
        self.assertEqual(cs2_warmup.pipeline_ticks(9, self.proc), 157)
        self.assertIsNone(cs2_warmup.pipeline_ticks(10, self.proc))

    def test_game_pids_and_map_open(self):
        for pid, comm in ((5, "cs2"), (6, "steam"), (7, "cs2x"), (8, "cs2.exe")):
            (self.proc / str(pid)).mkdir()
            (self.proc / str(pid) / "comm").write_text(comm + "\n")
        (self.proc / "self").mkdir()
        self.assertEqual(cs2_warmup.game_pids(self.proc), {5, 8})
        self.assertEqual(cs2_warmup.game_pids(self.proc, uid=os.getuid() + 1), set())
        (self.proc / "5/fd").mkdir()
        os.symlink("/g/csgo/maps/de_dust2_vanity.vpk", self.proc / "5/fd/3")
        self.assertFalse(cs2_warmup.map_open(5, "de_dust2", self.proc))
        os.symlink("/g/csgo/maps/de_dust2.vpk", self.proc / "5/fd/4")
        self.assertTrue(cs2_warmup.map_open(5, "de_dust2", self.proc))
        self.assertFalse(cs2_warmup.map_open(99, "de_dust2", self.proc))

    def test_steam_launch_alive(self):
        (self.proc / "8").mkdir()
        (self.proc / "8/cmdline").write_bytes(b"reaper\0SteamLaunch\0AppId=7300\0")
        self.assertFalse(cs2_warmup.steam_launch_alive(self.proc))
        (self.proc / "9").mkdir()
        (self.proc / "9/cmdline").write_bytes(b"reaper\0SteamLaunch\0AppId=730\0--\0x\0")
        self.assertTrue(cs2_warmup.steam_launch_alive(self.proc))
        self.assertFalse(cs2_warmup.steam_launch_alive(self.proc, uid=os.getuid() + 1))

    def test_launch_argv(self):
        argv = cs2_warmup.launch_argv(["steam"], "de_dust2")
        self.assertEqual(argv[:3], ["steam", "-applaunch", "730"])
        self.assertEqual(argv[-2:], ["+map", "de_dust2"])
        self.assertNotIn("-insecure", argv)
        for bad in ("de_dust2;quit", "+exec x", "", "../x"):
            with self.assertRaises(ValueError):
                cs2_warmup.launch_argv(["steam"], bad)

    def test_steam_command(self):
        which = lambda name: f"/usr/bin/{name}"
        self.assertEqual(cs2_warmup.steam_command(Path("/h/.local/share/Steam"), which),
                         ["/usr/bin/steam"])
        flat = Path("/h/.var/app/com.valvesoftware.Steam/data/Steam")
        self.assertEqual(cs2_warmup.steam_command(flat, which)[:2], ["flatpak", "run"])
        self.assertIsNone(cs2_warmup.steam_command(flat, lambda n: None))


class TestRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.proc, self.root = base / "proc", base / "Steam"
        self.proc.mkdir()
        self.cfg = self.root / "userdata/11/730/local/cfg/cs2_video.txt"
        self.cfg.parent.mkdir(parents=True)
        self.cfg.write_text("fullscreen 1\n")
        self.state = cs2_game.State(base / "state.json")
        self.clock = FakeClock()

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, game, maps, **kw):
        self.clock.hooks.append(game.tick)
        return cs2_warmup.Runner(self.root, self.root / cs2_game.GAME_DIR, "615.78.08", maps,
                                 self.state, ["steam"], proc=self.proc, spawn=game.spawn,
                                 kill=game.kill, clock=self.clock, poll=0, **kw)

    def test_runs_each_map_and_records(self):
        busy = lambda t: 5.0 if self.clock.now % 1000 < 100 else 0.0
        game = FakeGame(self.proc, self.clock, busy)

        def spawn(argv):
            FakeGame.spawn(game, argv)
            self.cfg.write_text("fullscreen 0\n")
            self.clock.now = (self.clock.now // 1000 + 1) * 1000

        game_spawn, game.spawn = game.spawn, spawn
        events = []
        self.runner(game, ["de_mirage", "de_dust2"]).run(
            lambda kind, **info: events.append((kind, info.get("name"))))
        self.assertEqual([a[-1] for a in game.launched], ["de_mirage", "de_dust2"])
        self.assertEqual([s for _p, s in game.killed], [signal.SIGTERM, signal.SIGTERM])
        self.assertEqual(self.state.maps_done("615.78.08"), {"de_mirage", "de_dust2"})
        self.assertTrue(self.state.complete("615.78.08"))
        info = self.state.data["maps"]["de_mirage"]
        self.assertEqual(info["result"], "settled")
        self.assertGreaterEqual(info["peak_cores"], 4.0)
        self.assertIn(("loading", "de_dust2"), events)
        self.assertEqual(events[-1], ("finished", None))
        self.assertEqual(self.cfg.read_text(), "fullscreen 1\n")
        self.assertFalse((self.proc / "100").exists())

    def test_waits_for_steam_to_see_the_game_stop(self):
        game = FakeGame(self.proc, self.clock, lambda t: 0.0)
        game.reaper_polls = 10_000
        with self.assertRaisesRegex(cs2_warmup.WarmupError, "steam-busy"):
            self.runner(game, ["de_dust2"]).run(lambda *a, **k: None)
        self.assertEqual(self.state.maps_done("615.78.08"), set())

    def test_refuses_forced_proton(self):
        (self.root / "config").mkdir(parents=True)
        (self.root / "config/config.vdf").write_text(
            '"InstallConfigStore"{"Software"{"Valve"{"Steam"{"CompatToolMapping"{'
            '"730"{"name" "GE-Proton11-1"}}}}}}')
        game = FakeGame(self.proc, self.clock)
        with self.assertRaisesRegex(cs2_warmup.WarmupError, "proton"):
            self.runner(game, ["de_dust2"]).run(lambda *a, **k: None)
        self.assertEqual(game.launched, [])

    def test_aborts_when_steam_starts_cs2_exe(self):
        game = FakeGame(self.proc, self.clock, lambda t: 5.0)

        def spawn(argv):
            FakeGame.spawn(game, argv)
            (game.dir / "comm").write_text("cs2.exe\n")

        game.spawn = spawn
        events = []
        with self.assertRaisesRegex(cs2_warmup.WarmupError, "proton"):
            self.runner(game, ["de_dust2"]).run(lambda kind, **i: events.append(kind))
        self.assertEqual(len(game.launched), 1)
        self.assertEqual(game.killed[0][1], signal.SIGTERM)
        self.assertFalse(game.dir.exists())
        self.assertNotIn("loading", events)
        self.assertEqual(self.state.maps_done("615.78.08"), set())

    def test_refuses_launch_options_that_join_a_server(self):
        cfg = self.root / "userdata/11/config/localconfig.vdf"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('"UserLocalConfigStore"{"Software"{"Valve"{"Steam"{"apps"{'
                       '"730"{"LaunchOptions" "+connect 10.0.0.1"}}}}}}')
        game = FakeGame(self.proc, self.clock)
        with self.assertRaisesRegex(cs2_warmup.WarmupError, "launch-options"):
            self.runner(game, ["de_dust2"]).run(lambda *a, **k: None)
        self.assertEqual(game.launched, [])

    def test_refuses_when_cs2_already_runs(self):
        (self.proc / "77").mkdir()
        (self.proc / "77/comm").write_text("cs2\n")
        game = FakeGame(self.proc, self.clock)
        with self.assertRaisesRegex(cs2_warmup.WarmupError, "cs2-running"):
            self.runner(game, ["de_dust2"]).run(lambda *a, **k: None)
        self.assertEqual(game.launched, [])

    def test_no_start_times_out(self):
        game = FakeGame(self.proc, self.clock)
        runner = self.runner(game, ["de_dust2"], start_timeout=30)
        runner.spawn = lambda argv: None
        with self.assertRaisesRegex(cs2_warmup.WarmupError, "no-start"):
            runner.run(lambda *a, **k: None)

    def test_cancel_stops_the_game(self):
        game = FakeGame(self.proc, self.clock, lambda t: 5.0)
        cancel = threading.Event()
        runner = self.runner(game, ["de_dust2"], cancel=cancel)

        def report(kind, **info):
            if kind == "compiling":
                cancel.set()

        with self.assertRaises(cs2_warmup.Cancelled):
            runner.run(report)
        self.assertEqual(len(game.killed), 1)
        self.assertFalse(game.dir.exists())
        self.assertEqual(self.state.maps_done("615.78.08"), set())

    def test_game_exit_is_an_error(self):
        game = FakeGame(self.proc, self.clock, lambda t: 5.0)
        runner = self.runner(game, ["de_dust2"])

        def report(kind, **info):
            if kind == "compiling":
                shutil.rmtree(game.dir)

        with self.assertRaisesRegex(cs2_warmup.WarmupError, "exited"):
            runner.run(report)


class FakeApp:
    def __init__(self):
        self.calls, self.next = [], 41

    def inhibit(self, window, flags, reason):
        self.next += 1
        self.calls.append(("inhibit", window, flags, reason))
        return self.next

    def uninhibit(self, cookie):
        self.calls.append(("uninhibit", cookie))


class TestWindowBehaviour(unittest.TestCase):
    def test_awake_holds_once_and_releases(self):
        app = FakeApp()
        awake = cs2_warmup_window.Awake(app, 12)
        awake.release()
        awake.hold("win", "why")
        awake.hold("win", "why")
        awake.release()
        awake.release()
        self.assertEqual(app.calls, [("inhibit", "win", 12, "why"), ("uninhibit", 42)])
        awake.hold("win", "again")
        self.assertEqual(awake.cookie, 43)

    def test_failed_inhibit_is_not_released(self):
        app = FakeApp()
        app.inhibit = lambda *a: 0
        awake = cs2_warmup_window.Awake(app, 4)
        awake.hold("win", "why")
        awake.release()
        self.assertEqual(app.calls, [])

    def test_window_is_fixed_size(self):
        self.assertIs(cs2_warmup_window.WINDOW["resizable"], False)
        self.assertEqual(cs2_warmup_window.WINDOW["width_request"],
                         cs2_warmup_window.WINDOW["default_width"])
        self.assertIn("Preparing CS2 shaders", RO)


class TestWindowStrings(unittest.TestCase):
    def test_romanian_for_every_message(self):
        for text in [*cs2_warmup_window.ERRORS.values(), cs2_warmup_window.INTRO]:
            self.assertIn(text, RO)
        self.assertEqual(RO["Preparing CS2 for your graphics card (one time only)"],
                         "Pregătesc CS2 pentru placa ta video (o singură dată)")

    def test_check_without_cs2(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(cs2_warmup_window.check(Path(tmp), False), 1)


if __name__ == "__main__":
    unittest.main()
