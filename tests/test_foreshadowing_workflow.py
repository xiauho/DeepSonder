from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from PySide6.QtCore import QCoreApplication, QObject, Signal

from core.foreshadowing import ForeshadowingStore
from core.project import NovelProject
from ui.ai_workflow_controller import (
    AIWorkflowController,
    PendingForeshadowingResolution,
)
from ui.project_session import ProjectSession


class FakeAIController(QObject):
    succeeded = Signal(object, object)


class FakeDocumentController(QObject):
    document_saved = Signal(str)


class ForeshadowingWorkflowTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_pending_resolution_is_applied_only_after_chapter_save_signal(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            note = ForeshadowingStore(project).create_note("古剑来历")
            session = ProjectSession()
            session.set_project(project)
            document_controller = FakeDocumentController()
            controller = AIWorkflowController(
                config={},
                project_session=session,
                editor=object(),
                document_controller=document_controller,
                ai_controller=FakeAIController(),
                ai_engine_controller=object(),
                inspector=object(),
                reports_page=object(),
                go_to_writing=lambda: True,
            )
            chapter_path = project.chapters_dir / "chapter_01.md"
            path_key = controller._path_key(chapter_path)
            controller._pending_foreshadowing_resolutions[path_key] = (
                PendingForeshadowingResolution(
                    project_root=str(project.root.resolve()).casefold(),
                    chapter_id="chapter_01",
                    note_ids=(note["id"],),
                )
            )

            self.assertEqual(
                ForeshadowingStore(project).get_note(note["id"])["status"],
                "open",
            )
            document_controller.document_saved.emit(str(chapter_path))

            resolved = ForeshadowingStore(project).get_note(note["id"])
            self.assertEqual(resolved["status"], "resolved")
            self.assertEqual(resolved["resolved_chapter"], "chapter_01")
            self.assertNotIn(path_key, controller._pending_foreshadowing_resolutions)
