# Glue live session: on the first console (tty1) auto-launch the installer.
# On any other tty (or after the installer exits) you get a normal shell.
# tty1 autologins as ROOT (see /etc/runit/sv/agetty-tty1/conf) so the
# installer and disk tools never depend on sudo; as_root() keeps every other
# login (glue user on tty2+, ssh) working too.
as_root() {
    if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi
}
case "$(tty)" in
/dev/tty1)
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
  Preview a window manager with:           startx        (apeturewm)
  Preview a Wayland compositor with:       niri

DONE
    fi
    ;;
*)
    command -v glue-install >/dev/null 2>&1 && \
        echo "Glue live — run 'glue-install' to install (root not needed: it self-elevates)."
    ;;
esac
