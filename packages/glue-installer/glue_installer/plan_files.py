"""Static file contents written by resolve_plan (gaming env, prime-run, target pacman.conf)."""

# Gaming installs only. NVIDIA's default shader disk cache is small and
# auto-purged, so big games (CS2 most visibly) recompile shaders on almost
# every launch. Keep the cache and never purge it; no-ops on AMD/Intel.
_GAMING_ENV_CONTENT = """\
# /etc/profile.d/glue-gaming.sh — Glue Linux gaming defaults.
export __GL_SHADER_DISK_CACHE=1
export __GL_SHADER_DISK_CACHE_SKIP_CLEANUP=1
export MESA_SHADER_CACHE_MAX_SIZE=12G
export __GL_SHADER_DISK_CACHE_SIZE=12000000000
"""

# Hybrid (Optimus) laptops enumerate the iGPU first, so Steam games default
# to it. Arch ships this wrapper as nvidia-prime; Artix has no such package,
# so the installer writes it. Steam Launch Options:  prime-run %command%
_PRIME_RUN_CONTENT = """\
#!/bin/sh
# prime-run — run a program on the NVIDIA dGPU (written by the Glue
# Linux installer; Artix has no nvidia-prime package).
__NV_PRIME_RENDER_OFFLOAD=1
__NV_PRIME_RENDER_OFFLOAD_PROVIDER=NVIDIA-G0
__GLX_VENDOR_LIBRARY_NAME=nvidia
__VK_LAYER_NV_optimus=NVIDIA_only
export __NV_PRIME_RENDER_OFFLOAD __NV_PRIME_RENDER_OFFLOAD_PROVIDER \\
    __GLX_VENDOR_LIBRARY_NAME __VK_LAYER_NV_optimus
exec "$@"
"""

# The installed system's /etc/pacman.conf. basestrap -C uses the LIVE conf
# (which also has the local file:// [glue] repo - that path does not exist
# on the installed disk), so the target gets its own conf with the online
# repos only. cachyos-keyring/cachyos-mirrorlist are in _ALWAYS_PACKAGES so
# these Includes resolve and future `pacman -Syu` keeps seeing the kernel repo.
_TARGET_PACMAN_CONF = """\
# /etc/pacman.conf — Glue Linux (installed system)
[options]
HoldPkg     = pacman glibc
Architecture = auto
CheckSpace
ParallelDownloads = 5
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional
# [cachyos] heroic-games-launcher replaces heroic-games-launcher-bin but
# needs electron43, which none of these repos has: it stopped every update
IgnorePkg   = heroic-games-launcher

# Glue's own packages (gluewc, glueqs, Glue Apps, the gaming pieces built
# without systemd) and their updates; first, so a package with the same name
# elsewhere never replaces them
[glue]
SigLevel = Required DatabaseOptional
Server = https://github.com/vladbiber/glue-repo/releases/download/$arch

[system]
Include = /etc/pacman.d/mirrorlist

[world]
Include = /etc/pacman.d/mirrorlist

[galaxy]
Include = /etc/pacman.d/mirrorlist

[lib32]
Include = /etc/pacman.d/mirrorlist

[cachyos]
Include = /etc/pacman.d/cachyos-mirrorlist
"""


def _build_target_pacman_conf(v3: bool) -> str:
    """Return the target /etc/pacman.conf, inserting [cachyos-v3] when v3 is active."""
    if not v3:
        return _TARGET_PACMAN_CONF
    # [cachyos-v3] packages are built for arch x86_64_v3
    return _TARGET_PACMAN_CONF.replace(
        "Architecture = auto\n", "Architecture = x86_64 x86_64_v3\n",
    ).replace(
        "[cachyos]\n",
        "[cachyos-v3]\nInclude = /etc/pacman.d/cachyos-v3-mirrorlist\n\n[cachyos]\n",
    )
