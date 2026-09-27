# Glue: neutral terminal text.
# Sourced by /etc/bash.bashrc when the user picked "neutral" at install.
# OSC 10 sets st's DEFAULT foreground to a soft warm light, so normal text is
# readable while the 16-colour amber accents and the fastfetch logo stay amber.
printf '\033]10;#d9c9a8\033\\'
