import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from core.project import NovelProject
from ui.project_session import ProjectSession
from ui.story_navigation_controller import StoryNavigationController


class _Editor:
    def __init__(self, category="章节"):
        self.category = category

    def current_category(self):
        return self.category


class _LeftPanel:
    def __init__(self):
        self.selected = []

    def select_path(self, path):
        self.selected.append(path)


class StoryNavigationControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_route_and_default_canon_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "project", "测试")
            session = ProjectSession()
            session.set_project(project)
            editor = _Editor()
            panel = _LeftPanel()
            controller = StoryNavigationController(
                project_session=session,
                editor=editor,
                left_panel=panel,
                show_route=lambda _route: None,
            )

            self.assertEqual(controller.route_for_category("章节"), "writing")
            self.assertEqual(controller.route_for_category("角色"), "canon")
            selected = controller.select_default_canon()

            self.assertEqual(selected, project.outline_dir / "main_arc.md")
            self.assertEqual(panel.selected, [selected])

    def test_open_memory_chapter_routes_and_selects_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "project", "测试")
            chapter = project.chapters_dir / "chapter-001.md"
            chapter.write_text("# 第一章\n", encoding="utf-8")
            session = ProjectSession()
            session.set_project(project)
            panel = _LeftPanel()
            routes = []
            controller = StoryNavigationController(
                project_session=session,
                editor=_Editor(),
                left_panel=panel,
                show_route=routes.append,
            )

            self.assertTrue(controller.open_memory_chapter("chapter-001"))
            self.assertEqual(routes, ["writing"])
            self.assertEqual(panel.selected, [chapter])
            self.assertFalse(controller.open_memory_chapter("missing"))


if __name__ == "__main__":
    unittest.main()
