import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from core.project import NovelProject
from ui.project_session import ProjectSession


class ProjectSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_load_publishes_project_and_data_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            changed = []
            data_changed = []
            session.project_changed.connect(lambda value: changed.append(value))
            session.data_changed.connect(lambda value: data_changed.append(value))

            loaded = session.load(project.root)
            session.notify_data_changed()

            self.assertEqual(loaded.name, "测试")
            self.assertIs(session.project, loaded)
            self.assertIsNotNone(session.data_store)
            self.assertIs(session.data_store.project, loaded)
            self.assertIs(session.data_store, session.data_store)
            self.assertEqual(changed, [loaded])
            self.assertEqual(data_changed, [loaded])

            session.clear()
            self.assertIsNone(session.project)
            self.assertIsNone(session.data_store)
            self.assertIsNone(changed[-1])

    def test_load_rejects_non_project_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session = ProjectSession()
            with self.assertRaises(ValueError):
                session.load(Path(tmp) / "missing")
