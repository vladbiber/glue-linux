# ~/.zshrc - Glue Linux
# Oh My Zsh is preinstalled for graphical sessions; minimal installs still work.
[[ -r /usr/share/glue/zshrc ]] && source /usr/share/glue/zshrc
# Run fastfetch on interactive shell start
[[ -o interactive ]] && command -v fastfetch >/dev/null && fastfetch
