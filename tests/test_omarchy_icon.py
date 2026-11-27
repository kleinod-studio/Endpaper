"""The icon templates for Omarchy's icon sets: well-formed once drawn, and only with colors Omarchy gives."""

import re
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "omarchy"
ROLES = {
    "icon_main", "icon_main_end", "icon_mark", "icon_paper", "icon_body", "icon_edge",
    "icon_ink", "icon_shadow", "icon_contrast", "icon_red", "icon_orange",
}
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


class IconTemplateTest(unittest.TestCase):
    def templates(self):
        found = sorted(DATA.glob("*/studio.kleinod.Endpaper.svg.tpl"))
        self.assertEqual([path.parent.name for path in found], ["arcade", "papercraft"])
        return found

    def test_only_known_roles(self):
        for path in self.templates():
            used = set(PLACEHOLDER.findall(path.read_text()))
            self.assertTrue(used, path)
            self.assertLessEqual(used, ROLES, path)

    def test_drawn_is_valid_svg(self):
        for path in self.templates():
            drawn = PLACEHOLDER.sub("#123456", path.read_text())
            root = ElementTree.fromstring(drawn)
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
            self.assertEqual(root.get("viewBox"), "0 0 512 512")

    def test_marked_for_the_script(self):
        # tools/omarchy-icon.sh only replaces or removes files with this marker.
        for path in self.templates():
            self.assertIn("endpaper-omarchy-icon", path.read_text())


if __name__ == "__main__":
    unittest.main()
