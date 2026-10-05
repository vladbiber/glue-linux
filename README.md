# Glue Linux

A light, Arch-based Linux distribution without systemd, tuned for gaming and
easy enough for someone who has never installed Linux.

![gluewc with the glueqs bar](packages/glue-installer/catalog/screenshots/gluewc-glueqs-bar.png)

- **No systemd:** pick **dinit** (default), **runit** or **OpenRC** at install time.
- **Kernel:** `linux-cachyos` (default) or `linux-zen`. On CPUs with
  x86-64-v3 the kernel and the gaming packages come from CachyOS's v3 builds.
- **Graphical installer:** Calamares with Glue's own pages for the network,
  the desktops, the kernel, the init system and gaming.
- **Seven desktops, install as many as you like:** gluewc + glueqs (default),
  gluewc + Noctalia, nvwm, KDE Plasma, XFCE, GNOME and Cinnamon. The login
  screen lets you pick one each time.
- **Glue Apps:** one store for the system repositories, Flathub, the AUR and
  AppImages, with a system update that keeps going when one package can't.
- **Gaming in one click:** Steam with Proton-CachyOS and GE-Proton, Heroic,
  Lutris, Wine, GameMode, MangoHud, Gamescope, the `scx_lavd` scheduler and
  ananicy-cpp priorities. Graphics drivers are picked for your card.

## Installing

Boot the ISO (Ventoy works). The live desktop opens **Glue Welcome**; press
**Install Glue Linux**. The installer asks, in this order:

1. **Network:** shows whether you are online and opens the Wi-Fi window. The
   install downloads its packages, so it needs a connection.
2. **Location, keyboard, partitions, user.** An existing EFI partition can be
   reused without formatting: when another system already boots with Limine
   from it, Glue adds its entries at the end of that menu and keeps a backup
   of the original `limine.conf`. Other operating systems found on the disks
   get their own entries.
3. **Desktop:** cards with a preview, how light each desktop is and how much
   memory it uses when idle. Tick one or more.
4. **Kernel** and **init system** (the defaults are fine for most people).
5. **Gaming:** yes or no.

The app store, Bluetooth, PipeWire, NetworkManager, zram swap, Wi-Fi
regulatory data and microcode are always installed. SSDs are mounted with
TRIM, everything with `noatime`.

### Graphics on the live system

The live desktop runs gluewc on your graphics card. If the card can't run it
(an unsupported or very old GPU), the installer opens in a simple full-screen
mode instead; the boot menu also has **NVIDIA off** and **safe graphics**
entries. When something goes wrong, `glue-debug --upload` in a terminal
collects the graphics and session logs and gives you a link to share.

Laptops with NVIDIA work both in hybrid mode (Intel or AMD drives the screen)
and with the MUX switched to the NVIDIA card. The installed system gets the
NVIDIA driver for your card's generation and a `prime-run` helper; put
`prime-run %command%` in a Steam game's launch options to run it on the
dedicated GPU of a hybrid laptop.

## After the install

- **Glue Welcome** opens at login (Super+Shift+F1 brings it back): the keys of
  the desktop you are in, an update button, system information and settings.
- **Glue Apps** ([separate repository](https://github.com/vladbiber/glue-apps))
  searches every source at once. *Update all* asks for the password once,
  clears a lock left by a power cut, refreshes the package keys, and if one
  package cannot be upgraded it leaves it for later and updates the rest.
  Flatpak and AUR apps are updated after the system, so a broken AUR recipe
  never blocks system updates.
- **Screen sharing** works on gluewc through the desktop portal (Discord,
  OBS, browsers).
- On the tiling desktops folders open in Files, pictures in Image Viewer,
  videos in Celluloid and PDFs in Papers, even when KDE or Cinnamon are
  installed next to them.

## gluewc and glueqs

[gluewc](https://github.com/vladbiber/gluewc) is the default Wayland
compositor: tiling with three layouts (bsp, scroll, drift) on one key, an
overview on a tap of Super, animations and blur.
[glueqs](https://github.com/vladbiber/glueqs) is its bar and shell: launcher,
notifications, network, volume and brightness panels (with sleep mode), media
controls and a settings window for gluewc itself.

## Building the ISO

You need Docker; the build runs in a container, so any host distro works.

```sh
git clone https://github.com/vladbiber/glue-linux.git
cd glue-linux
./build.sh
```

`build.sh` builds the `[glue]` packages from `packages/` and runs `buildiso`
on `iso-profile/glue/`. The ISO lands in `out/` (about 2.6 GB). Later runs
reuse the package cache; `GLUE_PKGS="gluewc glueqs" ./build.sh` rebuilds only
the packages you name.

Copy it to a Ventoy stick (`cp out/glue-runit-*.iso /run/media/$USER/Ventoy/ && sync`)
or write it with `dd`. To try it in a VM:

```sh
qemu-system-x86_64 -enable-kvm -m 6G -cdrom out/glue-runit-*-x86_64.iso
```

Prebuilt ISOs are published on the
[Releases page](https://github.com/vladbiber/glue-linux/releases).

## Repository layout

```
glue-linux/
├── build.sh, Dockerfile        # containerised ISO build
├── scripts/                    # make-iso.sh, headless checks and screenshot tools
├── iso-profile/glue/           # live ISO profile and overlay
└── packages/
    ├── glue-installer/         # install logic and the catalog of desktops/kernels
    ├── glue-calamares-config/  # Calamares pages, job modules and branding
    ├── glue-welcome/           # Welcome window and the Wi-Fi window
    ├── glue-apps/              # PKGBUILD for the store
    ├── glue-boot/              # Limine config generator and pacman hooks
    ├── glue-branding/          # wallpaper, os-release, terminal and rofi themes
    ├── glue-settings/          # sysctl and udev tuning
    ├── gluewc/, glueqs/, nvwm/ # desktops
    └── scx-scheds/, ananicy-cpp/, lib32-mangohud/, ... # gaming pieces without systemd
```

Adding a desktop, kernel or shell is an edit of
`packages/glue-installer/catalog/catalog.json`; the Calamares pages are
generated from it (`python -m glue_installer.calamares_config`).

## Tests

The install logic, the generated Calamares files, the boot menu generator and
Glue Welcome are covered by about 1100 tests that run without root:

```sh
sh scripts/gate.sh
```
