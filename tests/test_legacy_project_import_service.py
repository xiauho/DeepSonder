import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from application.legacy_project_import_service import (
    LegacyProjectImportError,
    LegacyProjectImportService,
)


FIXTURE_PROJECT = (
    Path(__file__).parent / "fixtures" / "electron_migration" / "golden_project"
)


def _digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


class LegacyProjectImportServiceTests(unittest.TestCase):
    def test_import_copies_only_identity_and_ordered_chapter_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "legacy"
            destination = workspace / "new-projects"
            shutil.copytree(FIXTURE_PROJECT, source)
            destination.mkdir()
            before = _digests(source)

            service = LegacyProjectImportService()
            preview = service.preview(source)
            project = service.create_project(
                destination,
                preview,
                name=preview.suggested_name,
                author=preview.suggested_author,
            )

            self.assertEqual(project.name, "迁移基线：雾港来信")
            self.assertEqual(project.meta["author"], "Novalist 测试")
            chapters = project.list_chapters()
            self.assertEqual([path.stem for path in chapters], ["chapter_0001", "chapter_0002"])
            first = chapters[0].read_text(encoding="utf-8")
            self.assertIn("# 第一章 雾中的信", first)
            self.assertIn("零点的雾笛", first)
            self.assertNotIn("作者备注", first)
            self.assertEqual(json.loads((project.memory_dir / "chapter_summaries.json").read_text(encoding="utf-8")), {})
            self.assertEqual(project.list_characters(), [])
            self.assertEqual(before, _digests(source))

    def test_import_rejects_destination_inside_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "legacy"
            shutil.copytree(FIXTURE_PROJECT, source)
            preview = LegacyProjectImportService().preview(source)
            with self.assertRaisesRegex(LegacyProjectImportError, "不能创建在旧项目"):
                LegacyProjectImportService().create_project(
                    source,
                    preview,
                    name="嵌套副本",
                )

    def test_import_rechecks_source_before_writing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "legacy"
            destination = workspace / "new-projects"
            shutil.copytree(FIXTURE_PROJECT, source)
            destination.mkdir()
            service = LegacyProjectImportService()
            preview = service.preview(source)
            chapter = source / "outline" / "chapters" / "chapter_01.md"
            chapter.write_text(chapter.read_text(encoding="utf-8") + "\n变化", encoding="utf-8")

            with self.assertRaisesRegex(LegacyProjectImportError, "发生变化"):
                service.create_project(destination, preview, name="不应创建")
            self.assertFalse((destination / "不应创建").exists())


if __name__ == "__main__":
    unittest.main()
