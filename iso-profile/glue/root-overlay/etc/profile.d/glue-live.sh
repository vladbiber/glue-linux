# Glue live session: tty1 autologins as `glue` (/etc/runit/sv/agetty-tty1/conf)
# and starts the gluewc desktop, which opens the graphical installer. There is
# no text installer on the live ISO; other ttys get a normal shell.
# inside the live desktop session: never start another one
[ -n "${GLUE_LIVE_SESSION:-}" ] && return 0 2>/dev/null
case "$(tty)" in
/dev/tty1)
    if [ -z "${GLUE_NOAUTO:-}" ] && command -v glue-live-session >/dev/null 2>&1; then
        export GLUE_NOAUTO=1
        glue-live-session
        rc=$?
        clear
        if [ "$rc" -eq 10 ]; then
            cat <<'MSG'

  No display output was found (no /dev/dri/card*), so the desktop and the
  installer cannot be shown. Any graphics works, including the one built
  into the processor; a dedicated graphics card is not needed.

  Check that a monitor is connected and that integrated graphics is not
  disabled in the firmware settings. Run 'glue-live-session' to try again.

MSG
        else
            cat <<'MSG'

  The live desktop was closed. Run 'glue-live-session' to start it again.

MSG
        fi
    fi
    ;;
esac
