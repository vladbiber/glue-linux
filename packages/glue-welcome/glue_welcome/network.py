"""NetworkManager helpers for glue-network: pure logic, no GTK, runner injectable."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

TIMEOUT = 10
CONNECT_TIMEOUT = 60

WRONG_PASSWORD_TEXT = "Wrong password or the network did not answer."
NO_NM_TEXT = "NetworkManager is not running, so Wi-Fi cannot be used."
SHORT_PASSWORD_TEXT = "Wi-Fi passwords have at least 8 characters."
BAD_PASSWORD_TEXT = "The password cannot contain line breaks."
ENTERPRISE_TEXT = "This network needs a username too. Use Advanced… to connect."
CONNECT_FAILED_TEXT = "Could not connect to this network."

CONNECTIVITY = ("full", "limited", "portal", "none", "unknown")


@dataclass(frozen=True)
class Network:
    ssid: str
    signal: int
    security: str
    in_use: bool

    @property
    def secured(self) -> bool:
        return bool(self.security)

    @property
    def enterprise(self) -> bool:
        return "802.1X" in self.security

    @property
    def signal_icon(self) -> str:
        return signal_icon(self.signal)


@dataclass
class Devices:
    wifi: bool = False
    wifi_connection: str = ""
    ethernet_connected: bool = False


@dataclass
class State:
    nm_running: bool = True
    wifi_device: bool = False
    wifi_on: bool = False
    ethernet: bool = False
    active_ssid: str = ""
    connectivity: str = "unknown"
    networks: list[Network] = field(default_factory=list)
    saved: set[str] = field(default_factory=set)


def run_command(argv: list[str], timeout: float = TIMEOUT) -> tuple[int, str, str]:
    """Default runner: (returncode, stdout, stderr), never raises."""
    env = dict(os.environ, LC_ALL="C")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              env=env, stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        return 127, "", str(exc)
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except OSError as exc:
        return 126, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def split_terse(line: str) -> list[str]:
    """Split one nmcli -t line on ':' honouring '\\:' and '\\\\' escapes."""
    fields, current, escaped = [], [], False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def signal_icon(signal: int) -> str:
    if signal >= 75:
        level = "excellent"
    elif signal >= 50:
        level = "good"
    elif signal >= 25:
        level = "ok"
    else:
        level = "weak"
    return f"network-wireless-signal-{level}-symbolic"


def parse_wifi_list(text: str) -> list[Network]:
    """Parse `nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list`."""
    best: dict[str, Network] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = split_terse(line)
        if len(parts) < 4:
            continue
        in_use, ssid, signal, security = parts[0], parts[1], parts[2], ":".join(parts[3:])
        if not ssid.strip():
            continue
        try:
            strength = max(0, min(100, int(signal)))
        except ValueError:
            strength = 0
        security = "" if security.strip() in ("", "--") else security.strip()
        net = Network(ssid, strength, security, in_use.strip() == "*")
        old = best.get(ssid)
        if old is None:
            best[ssid] = net
        else:
            keep = net if net.signal > old.signal else old
            best[ssid] = Network(keep.ssid, keep.signal, keep.security,
                                 old.in_use or net.in_use)
    return sorted(best.values(), key=lambda n: (not n.in_use, -n.signal, n.ssid.casefold()))


def parse_devices(text: str) -> Devices:
    """Parse `nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device`."""
    devices = Devices()
    for line in text.splitlines():
        parts = split_terse(line)
        if len(parts) < 3:
            continue
        kind, state = parts[1], parts[2]
        connected = state.startswith("connected") and "connecting" not in state
        if kind == "wifi":
            devices.wifi = True
            if connected and len(parts) > 3 and not devices.wifi_connection:
                devices.wifi_connection = parts[3]
        elif kind == "ethernet" and connected:
            devices.ethernet_connected = True
    return devices


def parse_radio(text: str) -> bool:
    return text.strip().lower() == "enabled"


def parse_connectivity(text: str) -> str:
    word = text.strip().lower()
    return word if word in CONNECTIVITY else "unknown"


def parse_saved_wifi(text: str) -> set[str]:
    """Names of saved Wi-Fi connections from `nmcli -t -f NAME,TYPE connection show`."""
    names = set()
    for line in text.splitlines():
        parts = split_terse(line)
        if len(parts) >= 2 and parts[1] in ("802-11-wireless", "wifi"):
            names.add(parts[0])
    return names


def snapshot(runner=run_command, rescan: bool = False) -> State:
    """Read everything the window shows in one go."""
    state = State()
    code, out, _err = runner(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
                             TIMEOUT)
    if code != 0:
        state.nm_running = False
        state.connectivity = "none"
        return state
    devices = parse_devices(out)
    state.wifi_device = devices.wifi
    state.ethernet = devices.ethernet_connected
    code, out, _err = runner(["nmcli", "radio", "wifi"], TIMEOUT)
    state.wifi_on = code == 0 and parse_radio(out)
    # the last state NetworkManager saw: "check" needs admin rights on an
    # installed system and fails with "Not authorized"
    code, out, _err = runner(["nmcli", "networking", "connectivity"], TIMEOUT)
    state.connectivity = parse_connectivity(out) if code == 0 else "unknown"
    if state.wifi_device and state.wifi_on:
        if rescan:
            runner(["nmcli", "device", "wifi", "rescan"], TIMEOUT)
        code, out, _err = runner(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY",
                                  "device", "wifi", "list", "--rescan", "no"], TIMEOUT)
        if code == 0:
            state.networks = parse_wifi_list(out)
        active = next((n.ssid for n in state.networks if n.in_use), "")
        state.active_ssid = active or devices.wifi_connection
        code, out, _err = runner(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
                                 TIMEOUT)
        if code == 0:
            state.saved = parse_saved_wifi(out)
    return state


def status_text(state: State) -> tuple[str, str]:
    """Headline and detail for the status row."""
    if not state.nm_running:
        return "Not connected", NO_NM_TEXT
    if state.active_ssid:
        name = f"Connected to {state.active_ssid}"
    elif state.ethernet:
        name = "Connected by cable"
    else:
        return "Not connected", ("Turn on Wi-Fi to see networks." if state.wifi_device
                                 and not state.wifi_on else "Pick a network below.")
    if state.connectivity == "full":
        return f"{name} — internet works", ""
    if state.connectivity == "portal":
        return name, "Open the web browser to sign in to this network."
    if state.connectivity == "limited":
        return name, "No internet yet. Wait a moment or try another network."
    return name, "Checking the internet…"


def set_wifi_radio(on: bool, runner=run_command) -> bool:
    code, _out, _err = runner(["nmcli", "radio", "wifi", "on" if on else "off"], TIMEOUT)
    return code == 0


def _key_mgmt(security: str) -> str:
    if "WPA" not in security and "WEP" in security:
        return "none"
    if "WPA3" in security and "WPA2" not in security and "WPA1" not in security:
        return "sae"
    return "wpa-psk"


def connect(network: Network, password: str = "", runner=run_command,
            tmpdir: str | None = None) -> str:
    """Connect to a network; returns an empty string on success or a friendly error."""
    ssid = network.ssid
    code, out, _err = runner(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"], TIMEOUT)
    if code == 0 and ssid in parse_saved_wifi(out) and not password:
        code, _out, _err = runner(["nmcli", "connection", "up", "id", ssid], CONNECT_TIMEOUT)
        return "" if code == 0 else CONNECT_FAILED_TEXT
    if not network.secured:
        code, _out, _err = runner(["nmcli", "device", "wifi", "connect", ssid], CONNECT_TIMEOUT)
        return "" if code == 0 else CONNECT_FAILED_TEXT
    if network.enterprise:
        return ENTERPRISE_TEXT
    if "\n" in password or "\r" in password:
        return BAD_PASSWORD_TEXT
    mgmt = _key_mgmt(network.security)
    if mgmt != "none" and len(password) < 8:
        return SHORT_PASSWORD_TEXT
    if code == 0 and ssid in parse_saved_wifi(out):
        runner(["nmcli", "connection", "delete", "id", ssid], TIMEOUT)
    key = "wep-key0" if mgmt == "none" else "psk"
    add = ["nmcli", "connection", "add", "type", "wifi", "ifname", "*", "con-name", ssid,
           "ssid", ssid, "wifi-sec.key-mgmt", mgmt]
    if mgmt == "none":
        add += ["wifi-sec.wep-key-type", "key"]
    code, _out, _err = runner(add, TIMEOUT)
    if code != 0:
        return CONNECT_FAILED_TEXT
    fd, path = tempfile.mkstemp(prefix="glue-network-", dir=tmpdir)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"802-11-wireless-security.{key}:{password}\n")
        code, _out, _err = runner(["nmcli", "connection", "up", "id", ssid,
                                   "passwd-file", path], CONNECT_TIMEOUT)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if code != 0:
        runner(["nmcli", "connection", "delete", "id", ssid], TIMEOUT)
        return WRONG_PASSWORD_TEXT
    return ""


def advanced_command(which=shutil.which) -> list[str] | None:
    if which("nm-connection-editor"):
        return ["nm-connection-editor"]
    if which("alacritty") and which("nmtui"):
        return ["alacritty", "-e", "nmtui"]
    return None
