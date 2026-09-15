import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from application.manuscript_import_service import (
    ManuscriptImportError,
    ManuscriptImportService,
)


FIXTURE_PROJECT = (
    Path(__file__).parent
    / "fixtures"
    / "electron_migration"
    / "golden_project"
)


def _digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


class ManuscriptImportServiceTests(unittest.TestCase):
    def test_v1_scan_reads_only_chapter_body_and_never_writes_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "legacy"
            shutil.copytree(FIXTURE_PROJECT, copied)
            before = _digests(copied)

            plan = ManuscriptImportService().scan(copied)

            self.assertEqual(plan.source_kind, "novalist_v1_manuscript")
            self.assertEqual(len(plan.chapters), 2)
            self.assertEqual(plan.chapters[0].chapter_id, "chapter_0001")
            self.assertEqual(plan.chapters[0].title, "第一章 雾中的信")
            self.assertIn("零点的雾笛", plan.chapters[0].content)
            self.assertNotIn("林砚在停用的邮局", plan.chapters[0].content)
            self.assertNotIn("作者备注", plan.chapters[0].content)
            self.assertEqual(before, _digests(copied))

    def test_external_scan_uses_natural_order_and_supported_chinese_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "chapter_10.md").write_text("# 第十章\n\n后章。\n", encoding="utf-8")
            (root / "chapter_2.txt").write_bytes("第二章正文。".encode("gb18030"))
            (root / "cover.png").write_bytes(b"not manuscript")

            plan = ManuscriptImportService().scan(root)

            self.assertEqual([item.source_name for item in plan.chapters], [
                "chapter_2.txt", "chapter_10.md"
            ])
            self.assertEqual(plan.chapters[0].encoding, "gb18030")
            self.assertEqual(plan.chapters[0].content, "第二章正文。\n")
            self.assertEqual(plan.chapters[1].content, "后章。\n")
            self.assertTrue(plan.digest.startswith("import-v1:"))

    def test_binary_or_empty_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ManuscriptImportError, "没有可识别"):
                ManuscriptImportService().scan(root)
            binary = root / "chapter.txt"
            binary.write_bytes(b"text\x00binary")
            with self.assertRaisesRegex(ManuscriptImportError, "二进制"):
                ManuscriptImportService().scan(binary)


if __name__ == "__main__":
    unittest.main()
