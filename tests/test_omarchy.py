import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from endpaper import omarchy  # noqa: E402


class OmarchyColorsTest(unittest.TestCase):
    def test_dark_theme(self):
        found = omarchy.parse({"mode": "dark", "background": "#2C2525", "foreground": "#e6d9db", "accent": "#f38d70",
                               "lighter_background": "#3d2f2a"})
        self.assertEqual(found, {"dark": True, "background": "#2c2525", "text": "#e6d9db", "accent": "#f38d70",
                                 "surface": "#3d2f2a"})

    def test_missing_colors_are_derived(self):
        found = omarchy.parse({"background": "#ffffff", "foreground": "#000000", "blue": "#1c71d8"})
        self.assertFalse(found["dark"])  # without a mode: by the background
        self.assertEqual(found["accent"], "#1c71d8")
        self.assertEqual(found["surface"], omarchy.mix("#ffffff", "#000000", 0.08))

    def test_without_text_or_background_there_is_no_theme(self):
        self.assertIsNone(omarchy.parse({"background": "#ffffff"}))
        self.assertIsNone(omarchy.parse({"background": "white", "foreground": "#000000"}))

    def test_read_from_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(omarchy.read(Path(folder)))
            (Path(folder) / "theme").mkdir()
            (Path(folder) / "theme" / "colors.toml").write_text('mode = "light"\nbackground = "#f4f1ea"\nforeground = "#30302e"\n')
            self.assertEqual(omarchy.read(Path(folder))["background"], "#f4f1ea")
            (Path(folder) / "theme" / "colors.toml").write_text("not = [toml")
            self.assertIsNone(omarchy.read(Path(folder)))

    def test_page_theme_and_window(self):
        found = omarchy.parse({"mode": "dark", "background": "#2b2a33", "foreground": "#ebe6f0", "accent": "#e07a5f"})
        page = omarchy.page_theme(found)
        self.assertEqual((page["bg"], page["scheme"], page["bgRgb"]), ("#2b2a33", "dark", "rgb(43, 42, 51)"))
        self.assertTrue(page["adapt"] and page["dark"])
        css = omarchy.window_css(found)
        self.assertIn("--view-bg-color: #2b2a33", css)
        self.assertIn("--accent-fg-color: #ffffff", css)


if __name__ == "__main__":
    unittest.main()
