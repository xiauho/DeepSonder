import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from core.project import NovelProject
from ui.project_lifecycle_controller import ProjectLifecycleController, ProjectSwitchCancelled
from ui.project_session import ProjectSession


class ProjectLifecycleControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_load_saves_switch_and_remembers_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            NovelProject.create(root, "测试")
            config = {"recent_projects": [], "last_project": ""}
            persisted = []
            session = ProjectSession()
            controller = ProjectLifecycleController(
                project_session=session,
                config=config,
                is_task_running=lambda: False,
                save_if_dirty=lambda: True,
                persist_config=lambda value: persisted.append(dict(value)),
            )

            loaded = controller.load(root)

            self.assertEqual(loaded.name, "测试")
            self.assertIs(session.project, loaded)
            self.assertEqual(config["last_project"], str(root.resolve()))
            self.assertEqual(len(persisted), 1)

    def test_load_rejects_running_task_and_unsaved_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            NovelProject.create(root, "测试")
            session = ProjectSession()
            running = ProjectLifecycleController(
                project_session=session,
                config={},
                is_task_running=lambda: True,
                save_if_dirty=lambda: True,
            )
            with self.assertRaises(RuntimeError):
                running.load(root)

            unsaved = ProjectLifecycleController(
                project_session=session,
                config={},
                is_task_running=lambda: False,
                save_if_dirty=lambda: False,
            )
            with self.assertRaises(ProjectSwitchCancelled):
                unsaved.load(root)

    def test_recent_projects_are_deduplicated_and_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            NovelProject.create(root, "测试")
            config = {
                "recent_projects": [str(root), str(root), str(Path(tmp) / "missing")],
                "last_project": str(Path(tmp) / "missing"),
            }
            persisted = []
            controller = ProjectLifecycleController(
                project_session=ProjectSession(),
                config=config,
                is_task_running=lambda: False,
                save_if_dirty=lambda: True,
                persist_config=lambda value: persisted.append(dict(value)),
            )

            paths = controller.recent_projects()

            self.assertEqual(paths, [root.resolve()])
            self.assertEqual(config["recent_projects"], [str(root.resolve())])
            self.assertEqual(config["last_project"], "")
            self.assertEqual(len(persisted), 1)


if __name__ == "__main__":
    unittest.main()
