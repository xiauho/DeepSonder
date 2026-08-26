import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QObject, QCoreApplication, Signal

from core.project import NovelProject
from core.project_data import ProjectDataStore
from ui.main_window import MainWindow


class _ImportanceEmitter(QObject):
    requested = Signal(str, str)


class _StatusMessage:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _ProjectSession:
    def __init__(self):
        self.notifications = []

    def notify_data_changed(self, paths, *, kind):
        self.notifications.append((tuple(paths), kind))


class _WindowHarness:
    set_system_importance = MainWindow.set_system_importance

    def __init__(self, project):
        self.project = project
        self.project_session = _ProjectSession()
        self.status_message = _StatusMessage()


class SystemImportanceWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_signal_persists_importance_and_publishes_targeted_refresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            target = project.canon_dir / "power" / "能力体系设定.md"
            window = _WindowHarness(project)
            emitter = _ImportanceEmitter()
            emitter.requested.connect(window.set_system_importance)

            emitter.requested.emit(str(target), "core")

            self.assertEqual(
                ProjectDataStore(project).system_metadata(target)["importance"],
                "core",
            )
            self.assertEqual(
                window.project_session.notifications,
                [((project.system_registry_path,), "system_importance")],
            )
            self.assertIn("后续 AI 任务将自动加载", window.status_message.text)


if __name__ == "__main__":
    unittest.main()
