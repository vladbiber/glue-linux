import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from glue_hub.catalog import (CatalogIndex, parse_appstream,
                              parse_flathub_search, parse_yay_info, parse_yay_search)
from glue_hub.model import App, AppSource, merge_apps


APPSTREAM = """<?xml version="1.0"?>
<components>
  <component type="desktop-application">
    <id>org.mozilla.firefox.desktop</id>
    <name>Firefox</name>
    <summary>Navigator web rapid</summary>
    <description><p>Explorează internetul în siguranță.</p></description>
    <developer_name>Mozilla</developer_name>
    <pkgname>firefox</pkgname>
    <project_license>MPL-2.0</project_license>
    <categories><category>Network</category><category>WebBrowser</category></categories>
    <screenshots><screenshot><image>https://example.test/firefox.png</image></screenshot></screenshots>
  </component>
  <component type="addon"><id>bad</id><name>Bad</name></component>
</components>"""


class TestAppStream(unittest.TestCase):
    def test_parses_graphical_entry(self):
        apps = parse_appstream(APPSTREAM, installed={"firefox"})
        self.assertEqual(len(apps), 1)
        app = apps[0]
        self.assertEqual(app.name, "Firefox")
        self.assertEqual(app.sources[0], AppSource("repo", "firefox", installed=True))
        self.assertEqual(app.screenshots, ("https://example.test/firefox.png",))
        self.assertIn("WebBrowser", app.categories)

    def test_invalid_xml_raises(self):
        with self.assertRaises(Exception):
            parse_appstream("<broken>")


class TestRemoteParsers(unittest.TestCase):
    def test_flathub_hit(self):
        apps = parse_flathub_search({"hits": [{
            "app_id": "org.videolan.VLC", "name": "VLC",
            "summary": "Video player", "icon": "https://example.test/vlc.svg",
            "screenshots": [{"sizes": [{"src": "small"}, {"src": "large"}]}],
            "categories": ["Video"],
        }]})
        self.assertEqual(apps[0].sources[0].kind, "flatpak")
        self.assertEqual(apps[0].screenshots, ("large",))

    def test_yay_output_with_ansi_and_descriptions(self):
        output = ("\x1b[1maur/vesktop 1.2.3-1 (+20)\x1b[0m\n"
                  "    Client Discord comunitar\n"
                  "aur/vscodium-bin 2.0-1 (+10)\n    Editor de cod\n")
        apps = parse_yay_search(output, installed={"vesktop"})
        self.assertEqual([a.name for a in apps], ["vesktop", "vscodium-bin"])
        self.assertTrue(apps[0].installed)
        self.assertEqual(apps[1].summary, "Editor de cod")

    def test_yay_info(self):
        app = parse_yay_info(
            "Repository : aur\nName : vesktop-bin\nVersion : 1.2-3\n"
            "Description : Discord client\nLicenses : GPL-3.0\n"
            "Maintainer : Glue Friend\nKeywords : chat discord\n",
            installed={"vesktop-bin"})
        self.assertEqual(app.name, "vesktop-bin")
        self.assertEqual(app.developer, "Glue Friend")
        self.assertEqual(app.license, "GPL-3.0")
        self.assertTrue(app.installed)


class TestMergeAndSearch(unittest.TestCase):
    def test_same_app_keeps_three_sources(self):
        apps = merge_apps([
            App("org.mozilla.firefox.desktop", "Firefox",
                sources=(AppSource("repo", "firefox"),)),
            App("org.mozilla.firefox", "Firefox",
                sources=(AppSource("flatpak", "org.mozilla.firefox"),)),
            App("aur.firefox", "Firefox", sources=(AppSource("aur", "firefox"),)),
        ])
        self.assertEqual(len(apps), 1)
        self.assertEqual([s.kind for s in apps[0].sources], ["repo", "flatpak", "aur"])

    def test_search_matches_description_and_filters_source(self):
        index = CatalogIndex([
            App("vlc", "VLC", "", "Redă orice video",
                sources=(AppSource("repo", "vlc"),)),
            App("chat", "Chat", "Mesaje", sources=(AppSource("flatpak", "x.chat"),)),
        ])
        self.assertEqual(index.search("video")[0].name, "VLC")
        self.assertEqual(index.search("", "flatpak")[0].name, "Chat")
        self.assertEqual(index.search("video", "aur"), [])

    def test_featured_preserves_curator_order(self):
        index = CatalogIndex([
            App("vlc", "VLC", sources=(AppSource("repo", "vlc"),)),
            App("firefox", "Firefox", sources=(AppSource("repo", "firefox"),)),
        ])
        self.assertEqual([a.name for a in index.featured(["Firefox", "Missing", "VLC"])],
                         ["Firefox", "VLC"])


if __name__ == "__main__":
    unittest.main()
