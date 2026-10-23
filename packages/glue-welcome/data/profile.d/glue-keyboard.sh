# Wayland compositors read XKB_DEFAULT_*; take them from the X11 keyboard
# config the installer and glue-keyboard write.
if [ -z "${XKB_DEFAULT_LAYOUT:-}" ] && [ -r "${GLUE_KEYBOARD_CONF:-/etc/X11/xorg.conf.d/00-keyboard.conf}" ]; then
    while IFS= read -r _glue_kb_line || [ -n "$_glue_kb_line" ]; do
        case "$_glue_kb_line" in
            *[Oo]ption*'"XkbLayout"'*)  _glue_kb_key=XKB_DEFAULT_LAYOUT ;;
            *[Oo]ption*'"XkbVariant"'*) _glue_kb_key=XKB_DEFAULT_VARIANT ;;
            *[Oo]ption*'"XkbOptions"'*) _glue_kb_key=XKB_DEFAULT_OPTIONS ;;
            *[Oo]ption*'"XkbModel"'*)   _glue_kb_key=XKB_DEFAULT_MODEL ;;
            *) continue ;;
        esac
        _glue_kb_val=${_glue_kb_line%\"*}
        _glue_kb_val=${_glue_kb_val##*\"}
        case "$_glue_kb_val" in
            *[!a-zA-Z0-9_:,\(\)-]*) continue ;;
        esac
        if [ -n "$_glue_kb_val" ]; then export "$_glue_kb_key=$_glue_kb_val"; fi
    done < "${GLUE_KEYBOARD_CONF:-/etc/X11/xorg.conf.d/00-keyboard.conf}"
    unset _glue_kb_line _glue_kb_key _glue_kb_val
fi
