import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from core.project import NovelProject
from core.project_schema import PROJECT_MANIFEST_RELATIVE_PATH
from ui.project_session import ProjectSession


class ProjectSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_load_publishes_project_and_data_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            self.assertFalse(hasattr(session, "data_changed"))
            changed = []
            data_changed = []
            session.project_changed.connect(lambda value: changed.append(value))
            session.data_change_detail.connect(
                lambda value, _change: data_changed.append(value)
            )

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

    def test_load_does_not_recreate_retired_style_guide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            project.style_guide_path.unlink(missing_ok=True)
            (project.root / PROJECT_MANIFEST_RELATIVE_PATH).unlink()

            session = ProjectSession()
            loaded = session.load(project.root)

            self.assertFalse(loaded.style_guide_path.is_file())
            self.assertTrue(session.last_migration_result.migrated)
