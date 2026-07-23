# Wheatley Linux

A small, amber-themed Linux distribution.

- **Base:** Artix Linux (Arch-based, **no systemd**) — pick **dinit**, **runit**
  or **OpenRC** at install time
- **Kernel:** `linux-cachyos` (CachyOS performance kernel, default) or `linux-zen`
- **Installer:** a full-screen **Python curses TUI** (`wheatley-install`) —
  catalog-driven wizard with Back navigation on every screen, automatic GeoIP
  timezone detection, network clock sync, and GPU driver auto-detection
- **Sessions (pick any, or minimal):** three custom X11 window managers
  (**apeturewm**, **nvwm**, **atomwm**), two Wayland compositors (**Niri** — with
  the optional **Noctalia** shell/bar — and **Sway**), plus optional full DEs:
  **KDE Plasma**, **GNOME (minimal)**, **XFCE**
- **Gaming Mode (optional):** Steam (+ Proton GE preinstalled), Heroic, Vulkan
  32/64-bit, GameMode, MangoHud, Gamescope, `prime-run` for hybrid NVIDIA
  laptops, and a persistent NVIDIA shader cache
- **Theme:** amber on near-black everywhere — TTY palette, `st`, GRUB theme,
  the installer and the greeter

Palette: background `#100A02`, secondary/lines `#A66900`, primary text `#F1B00A`.

## What the installer does

`wheatley-install` auto-starts on the live ISO (tty1) and walks you through:

1. **network** — required for every install; wired or Wi-Fi via `nmtui` (press
   `N`). As soon as you're online the installer detects your **local timezone**
   and syncs the live clock from the network before it ever touches the RTC
2. **mode** — custom install or **minimal** (bootable base only)
3. **kernel** — `linux-cachyos` (recommended) or `linux-zen`
4. **init system** — `dinit` (recommended), `runit` or `OpenRC`; every service
   is installed with the matching `-dinit`/`-runit`/`-openrc` script package
5. **sessions** — multi-select from the 8 WMs/DEs, each with ease/lightness
   ratings, keybinding cheat-sheet and screenshot path; Niri offers the
   Noctalia shell as an add-on (autostarted by the session wrapper)
6. **support toggles** — Bluetooth (BlueZ + Blueman)
7. **Gaming Mode** — the full Steam stack; the right Vulkan driver
   (NVIDIA open module / RADV / Intel ANV, 32-bit included) is pinned from the
   detected GPU instead of pacman's alphabetical default
8. **storage** — **erase a whole disk** (guided GPT, UEFI or BIOS), **install
   into an existing partition** (only that partition is formatted, the ESP is
   reused — dual-boot friendly), or **manual** via `cfdisk`
9. **identity** — hostname, user, password, locale, timezone (prefilled with
   the detected one)
10. a final summary — nothing is written until you type `yes`

The install itself is a single progress bar (full log in
`/tmp/wheatley-install.log`). Every install gets: a complete Artix base,
NetworkManager, `openntpd` (clock stays right from first boot), sudo wheel
setup, and a **Wheatley-branded GRUB** — `os-prober` dual-boot entries for the
other OSes on the machine, while the plugged-in install USB's own entries are
filtered out of the menu. Desktop installs add greetd + **tuigreet** on a
dedicated VT7, the PipeWire stack, `power-profiles-daemon`
(performance/balanced/power-saver in KDE/GNOME settings, `powerprofilesctl`
elsewhere), fonts, portals, and per-session wrappers that bring up D-Bus +
audio (+ `startx` for the X11 WMs). On NVIDIA machines the installer also
writes `/usr/local/bin/prime-run` (Artix has no `nvidia-prime` package) — set a
Steam game's launch options to `prime-run %command%` to run it on the dGPU.

## Layout

```
wheatley-linux/
├── build.sh                 # build the ISO on any Docker host
├── Dockerfile               # Artix + artools + CachyOS build env
├── scripts/make-iso.sh      # runs inside the container: repo + buildiso
├── repo/pacman.conf         # Artix + lib32 + CachyOS + [wheatley] repos
├── packages/                # custom packages (built into the [wheatley] repo)
│   ├── apeturewm/           # tiling WM (BSP, per-monitor bar)
│   ├── nvwm/                # BSP tiling WM, built-in bar, media keys enabled,
│   │                        #   user config at ~/.config/nvwm/config.conf
│   ├── atomwm/              # the lightest monocle WM (raw X11, ~150 KB RSS)
│   ├── st-wheatley/         # st patched to the Wheatley palette + JetBrains Mono
│   ├── proton-ge-custom-bin/# GE-Proton for Steam, preinstalled system-wide
│   ├── wheatley-installer/  # the Python curses installer + catalog
│   └── wheatley-branding/   # amber TTY palette, /etc/issue, os-release, GRUB theme
└── iso-profile/wheatley/    # artools profile (package lists + live overlay)
```

## The installer, under the hood

`packages/wheatley-installer/` is a small Python package with strictly pure
logic layers — `catalog.py` (JSON catalog + validation) → `plan.py` (selection
→ package/service/file plan) → `executor.py` (plan → ordered steps) →
`disks.py` / `identity.py` / `gpu.py` / `grub_filter.py` — driven by a curses
TUI (`ui_model.py` state machine, `render.py`, `tui.py`). All I/O is injected,
so the whole thing is covered by **470+ unit tests that run without root**:

```sh
cd packages/wheatley-installer
python3 -m unittest discover -s tests
```

Adding a session/kernel/shell is a catalog edit (`catalog/catalog.json`), not
a code change.

## Get the ISO

**Pick ONE of the two options below — you don't need both.**

- **Just want to run it?** → Option A (download).
- **Want to build it from source / change something?** → Option B (build).

### Option A — Download a prebuilt ISO (easiest)

The ISO is hosted on the Internet Archive (it is over GitHub's 2 GiB
per-file release limit):

- **Direct download:**
  [wheatley-runit-20260722-x86_64.iso](https://archive.org/download/wheatley-linux/wheatley-runit-20260722-x86_64.iso)
- Item page (with torrent): <https://archive.org/details/wheatley-linux>

Verify it:

```sh
sha256sum wheatley-runit-20260722-x86_64.iso
# a93c8d7acdfaf512e2df4c80a9b2105f7798295d387c5b0f0667491aa5d961e4
```

Then jump to [Putting the ISO on a USB stick](#putting-the-iso-on-a-usb-stick).
No build needed. Release notes and checksums also live on the
[Releases page](https://github.com/Vifuddyxg/wheatley-linux/releases).

### Option B — Build it yourself

Only needed if you want to compile the ISO from source. You need **Docker**
(the build runs in an Artix container, so it works from any distro — including a
Gentoo host). The image needs `--privileged` for loop devices / squashfs;
`build.sh` handles that.

```sh
git clone https://github.com/Vifuddyxg/wheatley-linux.git
cd wheatley-linux
./build.sh
```

The whole thing is self-contained: `build.sh` spins up the Artix + artools +
CachyOS container, builds the custom `[wheatley]` packages, and runs `buildiso`.
The custom window managers are cloned from GitHub during the build. First run
downloads packages and takes a while; re-runs reuse the `.pkgcache/` so they
are much faster.

The finished ISO lands in **`./out/`** (≈2.1 GB).

## Putting the ISO on a USB stick

**Ventoy** (recommended — just copy the file):

```sh
cp out/wheatley-runit-*-x86_64.iso /run/media/<you>/Ventoy/
sync          # IMPORTANT: wait for this to finish before unplugging
```

Or write the whole stick with `dd` (erases it):

```sh
sudo dd if=out/wheatley-runit-*-x86_64.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

Test it without hardware first:

```sh
qemu-system-x86_64 -enable-kvm -m 4G -bios /usr/share/edk2-ovmf/OVMF_CODE.fd \
    -cdrom out/wheatley-runit-*-x86_64.iso
```

On the live system you can also preview things before installing: `startx`
launches apeturewm, `niri` starts the Wayland compositor straight from a tty.

## Status / notes

- Boot-tested on real hardware (UEFI, hybrid Intel+NVIDIA laptop): all 8
  sessions start from the greeter, Steam runs on the Wayland compositors
  through each one's own XWayland integration (Niri auto-starts
  `xwayland-satellite`; Sway and Mutter ship their own).
- The installer is **online-only**: it always `basestrap`s a fresh system, so
  it can fit the CachyOS kernel and your chosen options. The network screen
  hard-blocks until you're connected.
- The live ISO itself runs runit (independent of the target's init) and
  auto-logs into the installer on tty1; every other tty is a normal shell.
- GNOME on Artix needs `gnome-session-sysvinit` (the init-agnostic session
  worker) — the catalog includes it, and the GNOME session coexists cleanly
  with a parallel KDE install (no gdm; greetd stays the greeter).
- `iso-profile/wheatley/profile.yaml` follows the current Artix `artools`
  iso-profiles (YAML) format. **artools changes these keys between versions** —
  if `buildiso` rejects a key, diff against the official `base` profile that
  `make-iso.sh` clones into place and adjust.

## The window managers

- **apeturewm** — tiling (BSP), per-monitor bar, workspaces, Wheatley palette
  baked in.
- **nvwm** — BSP tiling with a built-in per-monitor bar, multi-monitor via
  Xinerama + RandR hotplug, an overview mode, and a plain-text config
  (`~/.config/nvwm/config.conf` overrides `/etc/nvwm/config.conf`). Volume,
  media and brightness keys work out of the box.
- **atomwm** — the lightest possible monocle WM (raw X11 protocol on a socket,
  zero malloc, ~150 KB of RAM).

All three are cloned from GitHub at build time and ship a session wrapper that
starts X (via `startx`), a D-Bus session and PipeWire, so a TUI greeter like
tuigreet can launch them with working audio.
