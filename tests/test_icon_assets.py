import unittest
from pathlib import Path

from ui.icons import FONT_ROOT, MATERIAL_SYMBOL_FONT_FILES, STITCH_FONT_FILES


class IconAssetTests(unittest.TestCase):
    def test_runtime_font_directory_is_inside_public_assets(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        self.assertEqual(FONT_ROOT, project_root / "assets" / "fonts")

    def test_all_runtime_fonts_are_bundled(self) -> None:
        missing = [filename for filename in STITCH_FONT_FILES if not (FONT_ROOT / filename).is_file()]
        self.assertEqual(missing, [])

    def test_material_symbol_fonts_are_part_of_runtime_bundle(self) -> None:
        self.assertTrue(MATERIAL_SYMBOL_FONT_FILES.issubset(set(STITCH_FONT_FILES)))
        for filename in MATERIAL_SYMBOL_FONT_FILES:
            self.assertTrue((FONT_ROOT / filename).is_file())


if __name__ == "__main__":
    unittest.main()
