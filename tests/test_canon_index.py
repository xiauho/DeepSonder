import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path

from core.canon_schema import collect_canon_index, entry_status, parse_canon_entry
from core.project_data import CANON_ENTRY_TYPES
from tests.test_canon_schema import character_text


class FakeProject:
    """索引测试用的最小项目桩：canon/memory 目录与三类材料列表。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "canon" / "characters").mkdir(parents=True, exist_ok=True)
        (root / "canon" / "world").mkdir(parents=True, exist_ok=True)
        (root / "canon" / "power").mkdir(parents=True, exist_ok=True)
        self.memory_dir = root / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    @property
    def canon_dir(self) -> Path:
        return self.root / "canon"

    def list_characters(self) -> list[Path]:
        return sorted((self.canon_dir / "characters").glob("*.md"))

    def list_world(self) -> list[Path]:
        return sorted((self.canon_dir / "world").glob("*.md"))

    def list_power(self) -> list[Path]:
        return sorted((self.canon_dir / "power").glob("*.md"))


class CanonIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = FakeProject(Path(self.tmp.name))

    def _write(self, kind: str, name: str, text: str) -> Path:
        path = self.project.canon_dir / CANON_ENTRY_TYPES[kind]["directory"] / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_entry_status_missing_fields(self):
        entry = parse_canon_entry(
            "character",
            "# 白鸥\n\n## 基础档案\n\n- 姓名：白鸥\n- 别名：\n"
            "- 年龄／出生信息：\n- 性别：\n- 身份／阵营：\n- 首次出场：\n")
        self.assertEqual(entry_status(entry)["missing_fields"], 5)

    def test_entry_status_synced(self):
        filled = entry_status(parse_canon_entry("character", character_text()))
        self.assertTrue(filled["synced"])
        template = str(CANON_ENTRY_TYPES["character"]["template"]).format(title="新人")
        self.assertFalse(entry_status(parse_canon_entry("character", template))["synced"])

    def test_world_missing_sections(self):
        template = str(CANON_ENTRY_TYPES["world"]["template"]).format(title="北港")
        self.assertEqual(entry_status(parse_canon_entry("world", template))["missing_sections"], 7)

    def test_collect_canon_index_badges_and_identity(self):
        self._write("character", "白鸥", character_text())
        self._write("character", "林舟", "# 林舟\n\n## 基础档案\n\n- 姓名：林舟\n")
        self._write("world", "北港",
                    str(CANON_ENTRY_TYPES["world"]["template"]).format(title="北港"))
        index = collect_canon_index(self.project, alias_map={"林舟": "白鸥"})
        self.assertEqual([item["name"] for item in index], ["林舟", "白鸥", "北港"])
        self.assertEqual([item["kind"] for item in index],
                         ["character", "character", "world"])
        by_name = {item["name"]: item for item in index}
        self.assertEqual(by_name["林舟"]["identity"], "alias")
        self.assertEqual(by_name["白鸥"]["identity"], "primary")
        self.assertEqual(by_name["林舟"]["missing_fields"], 5)
        self.assertFalse(by_name["林舟"]["synced"])
        self.assertEqual(by_name["白鸥"]["missing_fields"], 0)
        self.assertTrue(by_name["白鸥"]["synced"])
        self.assertEqual(by_name["北港"]["missing_sections"], 7)
        self.assertEqual(by_name["白鸥"]["path"],
                         self.project.canon_dir / "characters" / "白鸥.md")

    def test_collect_canon_index_skips_unreadable_files(self):
        self._write("character", "白鸥", character_text())
        bad = self.project.canon_dir / "characters" / "坏档.md"
        bad.write_bytes(b"\xff\xfe\xfa broken")
        index = collect_canon_index(self.project)
        self.assertEqual([item["name"] for item in index], ["白鸥"])


if __name__ == "__main__":
    unittest.main()
