# Glue live session: on the first console (tty1) start the live desktop
# (glue-live-session: gluewc + glueqs + Glue Welcome) when the machine has KMS,
# otherwise - or when the desktop ends, or with `glue.tui` on the kernel
# command line - auto-launch the text installer. On any other tty (or after
# the installer exits) you get a normal shell.
# tty1 autologins as the `glue` user (see /etc/runit/sv/agetty-tty1/conf);
# glue-install elevates itself through sudoers.d/10-glue-live, and as_root()
# does the same for the few commands below.
as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}
case "$(tty)" in
/dev/tty1)
    # desktop first (once: GLUE_NOAUTO is set below, before the TUI starts);
    # it returns 10 when the TUI is wanted and 0 when the desktop was closed
    if [ -z "${GLUE_NOAUTO:-}" ] && command -v glue-live-session >/dev/null 2>&1; then
        glue-live-session || true
    fi
    clear
    cat <<'BANNER'

  Welcome to Glue Linux (live).

  Launching the installer...  (press Ctrl-C to drop to a shell instead)
  No network? Run 'nmtui' to connect, then 'glue-install'.

BANNER
    # only auto-start once, and only if the installer is present
    if [ -z "${GLUE_NOAUTO:-}" ] && command -v glue-install >/dev/null 2>&1; then
        export GLUE_NOAUTO=1
        # Keep kernel/daemon console messages off this tty: they scribble
        # over the curses installer (the TUI also self-repaints every second).
        as_root dmesg -n 1 2>/dev/null || true
        as_root setterm --msg off 2>/dev/null || true
        as_root glue-install || true
        cat <<'DONE'

  Installer exited. Re-run any time with:  glue-install
  Preview a window manager with:           startx        (nvwm)
  Preview a Wayland compositor with:       gluewc

DONE
    fi
    ;;
*)
    command -v glue-install >/dev/null 2>&1 && \
        echo "Glue live — run 'glue-install' to install (root not needed: it self-elevates)."
    ;;
esac
