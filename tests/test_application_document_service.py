import tempfile
import unittest
from pathlib import Path

from application.document_service import (
    DocumentPathError,
    DocumentRevisionConflict,
    DocumentService,
)
from core.project import NovelProject
from core.project_data import ProjectDataStore


class DocumentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DocumentService()

    def test_timeline_notes_have_a_stable_display_title_without_changing_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            path = project.canon_dir / "timeline.md"
            opened = self.service.open_document(project, "时间线", path)
            self.assertEqual(opened.title, "时间线笔记")
            self.assertEqual(opened.content, "")
            notes = "# 自己的笔记标题\n\n出发前的设想。"
            saved = self.service.save_document(
                project, "时间线", path, notes, expected_revision=opened.revision,
            )
            self.assertEqual(saved.title, "时间线笔记")
            reopened = DocumentService().open_document(project, "时间线", path)
            self.assertEqual(reopened.title, "时间线笔记")
            self.assertEqual(reopened.content, notes)
            self.assertEqual(path.read_text(encoding="utf-8"), notes)

    def test_open_and_save_return_versioned_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            chapter = project.chapters_dir / "chapter_01.md"

            opened = self.service.open_document(project, "章节", chapter)
            saved = self.service.save_document(
                project,
                "章节",
                chapter,
                "# 新标题\n\n## 正文\n新内容\n",
                expected_revision=opened.revision,
            )

            self.assertEqual(opened.relative_path, "outline/chapters/chapter_01.md")
            self.assertTrue(opened.revision.startswith("v1:"))
            self.assertEqual(saved.title, "新标题")
            self.assertNotEqual(saved.revision, opened.revision)
            self.assertEqual(chapter.read_text(encoding="utf-8"), saved.content)

    def test_save_detects_external_revision_and_force_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            chapter = project.chapters_dir / "chapter_01.md"
            opened = self.service.open_document(project, "章节", chapter)
            chapter.write_text("外部版本", encoding="utf-8")

            with self.assertRaises(DocumentRevisionConflict) as raised:
                self.service.save_document(
                    project,
                    "章节",
                    chapter,
                    "本地版本",
                    expected_revision=opened.revision,
                )
            self.assertEqual(raised.exception.path, chapter.resolve())
            self.assertEqual(chapter.read_text(encoding="utf-8"), "外部版本")

            saved = self.service.save_document(
                project,
                "章节",
                chapter,
                "本地版本",
                expected_revision=opened.revision,
                force=True,
            )
            self.assertEqual(saved.content, "本地版本")

    def test_document_paths_are_confined_to_editable_project_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            outside = Path(tmp) / "outside.md"
            outside.write_text("不属于项目", encoding="utf-8")

            with self.assertRaises(DocumentPathError):
                self.service.open_document(project, "章节", outside)
            with self.assertRaises(DocumentPathError):
                self.service.open_document(project, "配置", project.root / "project.json")

    def test_create_import_and_delete_operations_publish_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            store = ProjectDataStore(project)

            chapter = self.service.create_chapter(
                project, store, "第二章", "chapter_02"
            )
            character = self.service.create_canon_entry(
                project, store, "character", "林夜"
            )
            source = Path(tmp) / "外部章.md"
            source.write_text("导入正文", encoding="utf-8")
            imported = self.service.import_markdown(project, store, [source])

            self.assertEqual(chapter.kind, "chapter")
            self.assertTrue(Path(chapter.result_path).is_file())
            self.assertEqual(character.kind, "canon")
            self.assertTrue(Path(character.result_path).is_file())
            self.assertEqual(len(imported), 1)
            self.assertIn("# 外部章", Path(imported[0].result_path).read_text(encoding="utf-8"))

            deleted = self.service.delete_chapter(
                project, store, Path(chapter.result_path).stem
            )
            self.assertEqual(deleted.kind, "chapter")
            self.assertFalse(Path(chapter.result_path).exists())


if __name__ == "__main__":
    unittest.main()
