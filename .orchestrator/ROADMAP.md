# Roadmap — Wheatley Linux (new installer)

> Wheatley Linux — a personal, non-commercial Arch/Artix-based Linux distribution.

Depth-first: each capability must work end-to-end (installs & boots), tested,
before the next. Reuse the existing ISO build (Docker + artools, build.sh) and
custom packages (apeturewm, nvwm, branding). Amber palette #100A02/#A66900/#F1B00A.

## Capabilities

### 1. Installer shell — friendly, composable
- [ ] New installer flow that COMPOSES the system from clear choices.
- [ ] For every WM/DE: show ease-of-use, lightness, and keybindings.
- [ ] Show a SCREENSHOT preview per WM/DE if at all possible.
- [ ] Prominent message on the WM/DE step: "You can select MULTIPLE — pick which
      one to use at the login screen. You can have several at once."

### 2. Core defaults
- [ ] dinit as the RECOMMENDED init (labeled "recommended").
- [ ] New terminals auto-run fastfetch (shell rc for bash/zsh).

### 3. Kernel choice
- [ ] Primary: CachyOS kernel.
- [ ] Secondary option: Zen kernel.

### 4. Support toggles
- [ ] Bluetooth checkbox: installs bluez + service + a Bluetooth GUI app (Blueman/Overskride).

### 5. Gaming (optional)
- [ ] "Gaming Mode" toggle: makes the system plug-and-play for gaming.
- [ ] Preinstall: Steam, Heroic, Proton-GE, Vulkan + shaders, gamemode/mangohud.
- [ ] Auto-detect + install GPU drivers.

### 6. Minimal install (optional)
- [ ] Bare-system path with no WM/DE preselected.

### 7. Window managers (multi-select, choose at login)
- [ ] apeturewm (Aperture-themed) — with ease/lightness/keybinds + screenshot.
- [ ] nvwm (light & strong) — with ease/lightness/keybinds + screenshot.
- [ ] Mangowc (user-friendly) — CHOOSE the bar/shell:
      - [ ] Noctalia shell, or
      - [ ] ilyamiro Quickshell "imperative-dots" (github.com/ilyamiro/imperative-dots;
            local ~/src/nixos-configuration, running at ~/.config/hypr/scripts/quickshell) —
            currently runs on this machine's Mangowc; v2.0.0 is a portable QS config
            supporting Niri/MangoWM. Reuse ilyamiro previews/*.png as screenshots.
      - [ ] Show ease/lightness/keybinds for whichever bar/shell is picked.
- [ ] Niri (user-friendly) — same bar/shell CHOICE: Noctalia shell OR ilyamiro Quickshell.
- [ ] Sway (minimal Wayland) — with details + screenshot.
- [ ] Login screen (greeter) lets the user pick among installed sessions.

### 8. Desktop environments — "de slop" (optional, clearly separated)
- [ ] KDE Plasma — with ease/lightness/keybinds + screenshot.
- [ ] XFCE — with ease/lightness/keybinds + screenshot.

_The architect refines and orders these on the first cycle._
