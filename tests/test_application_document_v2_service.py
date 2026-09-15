import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from application.document_service import DocumentRevisionConflict
from application.document_v2_service import (
    APPEND_IMPORT_JOURNAL,
    DocumentV2Service,
    SAVE_JOURNAL,
)
from application.manuscript_import_service import ManuscriptImportService
from application.project_v2_service import ProjectV2Service
from core.storage import atomic_write_text as real_atomic_write_text


class DocumentV2ServiceTests(unittest.TestCase):
    def _project(self, root: Path):
        source = root / "source.md"
        source.write_text("# 第一章\n\n原始正文。\n", encoding="utf-8")
        output = root / "output"
        output.mkdir()
        plan = ManuscriptImportService().scan(source)
        return ProjectV2Service().create_project(output, "新项目", import_plan=plan)

    def test_open_save_and_external_revision_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service = DocumentV2Service()
            item = service.snapshot(project).chapters[0]
            opened = service.open_document(project, item.chapter_id)

            saved = service.save_document(
                project,
                item.chapter_id,
                "# 第一章 新标题\n\n修改后的正文。",
                expected_revision=opened.revision,
            )
            self.assertEqual(saved.title, "第一章 新标题")
            self.assertIn("修改后的正文", saved.content)
            self.assertNotEqual(saved.revision, opened.revision)

            Path(saved.path).write_text("# 外部版本\n", encoding="utf-8")
            with self.assertRaises(DocumentRevisionConflict):
                service.save_document(
                    project,
                    item.chapter_id,
                    "# 本地版本\n",
                    expected_revision=saved.revision,
                )
            self.assertEqual(Path(saved.path).read_text(encoding="utf-8"), "# 外部版本\n")

    def test_failed_index_write_rolls_back_chapter_and_clears_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service = DocumentV2Service()
            opened = service.open_document(project, "chapter_0001")
            original = Path(opened.path).read_bytes()
            failed_once = False

            def flaky_write(path, text, *, encoding="utf-8"):
                nonlocal failed_once
                if Path(path).name == "index.json" and not failed_once:
                    failed_once = True
                    raise OSError("simulated index failure")
                return real_atomic_write_text(Path(path), text, encoding=encoding)

            with patch("application.document_v2_service.atomic_write_text", side_effect=flaky_write):
                with self.assertRaises(OSError):
                    service.save_document(
                        project,
                        "chapter_0001",
                        "# 新内容\n",
                        expected_revision=opened.revision,
                    )

            self.assertEqual(Path(opened.path).read_bytes(), original)
            self.assertFalse((project.root / SAVE_JOURNAL).exists())
            ProjectV2Service.open_project(project.root)

    def test_chapter_lifecycle_preserves_stable_ids_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service = DocumentV2Service()

            snapshot, created = service.create_chapter(
                project, "第二章", after_chapter_id="chapter_0001"
            )
            self.assertEqual([item.chapter_id for item in snapshot.chapters], [
                "chapter_0001", "chapter_0002",
            ])
            renamed_snapshot, renamed = service.rename_chapter(
                project, "chapter_0002", "第二章：回声",
                expected_revision=created.revision,
            )
            self.assertEqual(renamed_snapshot.chapters[1].title, "第二章：回声")
            self.assertTrue(renamed.content.startswith("# 第二章：回声\n"))

            reordered = service.reorder_chapters(
                project, ["chapter_0002", "chapter_0001"]
            )
            self.assertEqual(
                [(item.chapter_id, item.sequence) for item in reordered.chapters],
                [("chapter_0002", 1), ("chapter_0001", 2)],
            )

            after_delete, trash, deleted = service.delete_chapter(project, "chapter_0001")
            self.assertEqual([item.chapter_id for item in after_delete.chapters], ["chapter_0002"])
            self.assertEqual(trash.items[0].trash_id, deleted.trash_id)
            self.assertFalse((project.root / "manuscript" / "chapter_0001.md").exists())

            after_create, newest = service.create_chapter(project, "第三章")
            self.assertEqual(newest.relative_path, "manuscript/chapter_0003.md")
            self.assertEqual(after_create.item_count, 2)

            restored, empty_trash, restored_document = service.restore_chapter(
                project, deleted.trash_id
            )
            self.assertEqual(empty_trash.items, ())
            self.assertEqual(restored_document.relative_path, "manuscript/chapter_0001.md")
            self.assertEqual(
                [item.chapter_id for item in restored.chapters],
                ["chapter_0002", "chapter_0001", "chapter_0003"],
            )
            ProjectV2Service.open_project(project.root)

    def test_invalid_reorder_does_not_change_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service = DocumentV2Service()
            before = service.snapshot(project)

            with self.assertRaises(ValueError):
                service.reorder_chapters(project, ["chapter_0001", "chapter_0001"])

            self.assertEqual(service.snapshot(project), before)

    def test_append_import_records_provenance_and_exports_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            source = root / "append"
            source.mkdir()
            (source / "02.md").write_text("# 第二章\n\n第二章正文。\n", encoding="utf-8")
            (source / "03.md").write_text("# 第三章\n\n第三章正文。\n", encoding="utf-8")
            plan = ManuscriptImportService().scan(source)
            service = DocumentV2Service()

            snapshot, opened = service.append_import(
                project, plan, after_chapter_id="chapter_0001"
            )

            self.assertEqual(snapshot.item_count, 3)
            self.assertEqual(opened.relative_path, "manuscript/chapter_0002.md")
            imports = json.loads(
                (project.root / "provenance" / "imports.json").read_text(encoding="utf-8")
            )["imports"]
            self.assertEqual(imports[-1]["mode"], "append")
            self.assertEqual(imports[-1]["chapter_ids"], ["chapter_0002", "chapter_0003"])

            markdown_path = root / "book.md"
            markdown = service.export_manuscript(project, markdown_path, format_name="md")
            markdown_text = markdown_path.read_text(encoding="utf-8-sig")
            self.assertEqual(markdown.chapter_count, 3)
            self.assertTrue(markdown.sha256)
            self.assertLess(markdown_text.index("第一章"), markdown_text.index("第二章正文"))
            self.assertLess(markdown_text.index("第二章正文"), markdown_text.index("第三章正文"))
            self.assertIn("## 目录", markdown_text)

            text_path = root / "book.txt"
            service.export_manuscript(project, text_path, format_name="txt")
            plain_text = text_path.read_text(encoding="utf-8-sig")
            self.assertNotIn("# ", plain_text)
            self.assertIn("第二章正文。", plain_text)

    def test_append_import_rejects_duplicate_and_rolls_back_failed_provenance_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = self._project(root)
            service = DocumentV2Service()
            original_plan = ManuscriptImportService().scan(root / "source.md")
            with self.assertRaisesRegex(ValueError, "已经导入"):
                service.append_import(project, original_plan)

            source = root / "new.md"
            source.write_text("# 新章节\n\n追加内容。\n", encoding="utf-8")
            plan = ManuscriptImportService().scan(source)
            failed_once = False

            def flaky_write(path, text, *, encoding="utf-8"):
                nonlocal failed_once
                if Path(path).as_posix().endswith("provenance/imports.json") and not failed_once:
                    failed_once = True
                    raise OSError("simulated provenance failure")
                return real_atomic_write_text(Path(path), text, encoding=encoding)

            with patch("application.document_v2_service.atomic_write_text", side_effect=flaky_write):
                with self.assertRaises(OSError):
                    service.append_import(project, plan)

            self.assertEqual(service.snapshot(project).item_count, 1)
            self.assertFalse((project.root / "manuscript" / "chapter_0002.md").exists())
            self.assertFalse((project.root / APPEND_IMPORT_JOURNAL).exists())
            ProjectV2Service.open_project(project.root)


if __name__ == "__main__":
    unittest.main()
