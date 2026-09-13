import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from application.document_service import DocumentRevisionConflict
from application.document_v2_service import DocumentV2Service, SAVE_JOURNAL
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


if __name__ == "__main__":
    unittest.main()
