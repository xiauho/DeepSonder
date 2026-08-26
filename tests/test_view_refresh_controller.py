import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from core.project import NovelProject
from ui.project_session import ProjectSession
from ui.view_refresh_controller import ViewRefreshController


class _ProjectView:
    def __init__(self):
        self.projects = []
        self.refreshes = []
        self.render_count = 0
        self.importance_refresh_count = 0

    def set_project(self, project):
        self.projects.append(project)

    def show_project(self, project):
        self.projects.append(project)

    def refresh(self, project):
        self.refreshes.append(project)

    def render_preview(self):
        self.render_count += 1

    def refresh_system_importance(self):
        self.importance_refresh_count += 1


class _Inspector:
    def __init__(self):
        self.calls = []

    def show_project(self, project, chapter_id=None):
        self.calls.append((project, chapter_id))


class _Editor:
    def __init__(self):
        self.cleared_with = []
        self.chapter_id = None

    def clear_document(self, placeholder):
        self.cleared_with.append(placeholder)

    def current_chapter_id(self):
        return self.chapter_id


class ViewRefreshControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _controller(self, session, editor):
        left_panel = _ProjectView()
        inspector = _Inspector()
        dashboard = _ProjectView()
        memory = _ProjectView()
        reports = _ProjectView()
        export = _ProjectView()
        controller = ViewRefreshController(
            session,
            editor,
            left_panel,
            inspector,
            dashboard,
            memory,
            reports,
            export,
        )
        return controller, left_panel, inspector, dashboard, memory, reports, export

    def test_project_switch_and_data_change_refresh_all_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            editor = _Editor()
            controller, left_panel, inspector, dashboard, memory, reports, export = self._controller(
                session, editor
            )

            session.set_project(project)
            self.assertEqual(editor.cleared_with[-1], "选择一份故事资料")
            self.assertIs(left_panel.projects[-1], project)
            self.assertIs(memory.projects[-1], project)
            self.assertIs(reports.projects[-1], project)
            self.assertIs(export.projects[-1], project)
            self.assertIs(dashboard.refreshes[-1], project)
            self.assertEqual(inspector.calls[-1], (project, None))

            session.notify_data_changed()
            self.assertIs(left_panel.projects[-1], project)
            self.assertEqual(inspector.calls[-1], (project, None))
            self.assertGreaterEqual(len(dashboard.refreshes), 2)

            # Keep the controller alive for the signal assertions above.
            self.assertIsNotNone(controller)

    def test_current_context_uses_open_chapter_and_refreshes_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            session.set_project(project)
            editor = _Editor()
            controller, _left, inspector, dashboard, _memory, _reports, export = self._controller(
                session, editor
            )
            editor.chapter_id = "chapter-001"

            controller.refresh_current_context()

            self.assertEqual(inspector.calls[-1], (project, "chapter-001"))
            self.assertIs(dashboard.refreshes[-1], project)
            self.assertEqual(export.render_count, 1)

    def test_saved_file_refreshes_radar_without_rebuilding_project_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            session.set_project(project)
            editor = _Editor()
            controller, _left, inspector, dashboard, _memory, _reports, export = self._controller(
                session, editor
            )
            dashboard_count = len(dashboard.refreshes)
            chapter_path = project.chapters_dir / "chapter_01.md"

            controller.refresh_current_context(str(chapter_path))

            self.assertEqual(inspector.calls[-1], (project, None))
            self.assertEqual(len(dashboard.refreshes), dashboard_count)
            self.assertEqual(export.render_count, 0)

    def test_targeted_canon_change_does_not_refresh_unrelated_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            session.set_project(project)
            editor = _Editor()
            controller, left_panel, inspector, dashboard, memory, reports, export = self._controller(
                session, editor
            )
            before = {
                "left": len(left_panel.projects),
                "dashboard": len(dashboard.refreshes),
                "memory": len(memory.projects),
                "reports": len(reports.projects),
                "export": len(export.projects),
            }
            canon_path = project.canon_dir / "timeline.md"

            session.notify_data_changed([canon_path], kind="canon")

            self.assertEqual(len(left_panel.projects), before["left"] + 1)
            self.assertEqual(len(dashboard.refreshes), before["dashboard"])
            self.assertEqual(len(memory.projects), before["memory"])
            self.assertEqual(len(reports.projects), before["reports"])
            self.assertEqual(len(export.projects), before["export"])
            self.assertEqual(len(inspector.calls), 1)
            self.assertIsNotNone(controller)

    def test_system_importance_change_refreshes_badges_without_rebuilding_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            session.set_project(project)
            editor = _Editor()
            controller, left_panel, inspector, dashboard, memory, reports, export = self._controller(
                session, editor
            )
            before_projects = len(left_panel.projects)

            session.notify_data_changed(
                [project.system_registry_path], kind="system_importance"
            )

            self.assertEqual(left_panel.importance_refresh_count, 1)
            self.assertEqual(len(left_panel.projects), before_projects)
            self.assertEqual(len(dashboard.refreshes), 0)
            self.assertEqual(len(memory.projects), 0)
            self.assertEqual(len(reports.projects), 0)
            self.assertEqual(len(export.projects), 0)
            self.assertEqual(len(inspector.calls), 1)
            self.assertIsNotNone(controller)


if __name__ == "__main__":
    unittest.main()
