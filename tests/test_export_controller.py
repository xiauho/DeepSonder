import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from core.project import NovelProject
from ui.export_controller import ExportController
from ui.project_session import ProjectSession


class ExportControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_export_controller_renders_and_writes_selected_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试作品")
            chapter = project.chapters_dir / "chapter_02.md"
            chapter.write_text(
                "# 第二章\n\n## 大纲\n规划\n\n## 正文\n第二章正文。\n",
                encoding="utf-8",
            )
            session = ProjectSession()
            session.set_project(project)
            controller = ExportController(session)
            options = {
                "chapter_ids": ["chapter_02"],
                "include_title": True,
                "include_toc": True,
                "separators": False,
                "strip": False,
                "format": "md",
            }
            output = Path(tmp) / "book.txt"

            result = controller.export(options, output, format_name="txt")

            self.assertEqual(result.chapter_count, 1)
            self.assertEqual(result.format, "txt")
            self.assertEqual(result.path, output)
            text = output.read_text(encoding="utf-8-sig")
            self.assertIn("测试作品", text)
            self.assertIn("第二章正文。", text)
            self.assertNotIn("## 大纲", text)

    def test_export_requires_at_least_one_existing_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试作品")
            session = ProjectSession()
            session.set_project(project)
            controller = ExportController(session)

            with self.assertRaises(ValueError):
                controller.export(
                    {"chapter_ids": ["missing"], "format": "md"},
                    Path(tmp) / "book.md",
                )
