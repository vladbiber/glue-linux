"""Curated home-page ordering and friendly AppStream categories."""

RECOMMENDED = (
    "Firefox", "LibreOffice", "VLC", "GIMP", "Discord", "Spotify", "OBS",
    "Steam", "Lutris", "Thunderbird", "Telegram", "Kdenlive", "Inkscape",
    "Blender", "Krita", "qBittorrent", "VSCodium", "Bitwarden", "Signal",
)

CATEGORIES = {
    "Toate categoriile": (), "Internet": ("Network", "WebBrowser"),
    "Birou": ("Office",), "Media": ("AudioVideo", "Audio", "Video"),
    "Chat": ("Chat", "InstantMessaging"), "Jocuri": ("Game",),
    "Grafică": ("Graphics",), "Dezvoltare": ("Development",),
    "Utilitare": ("Utility", "System"),
}
