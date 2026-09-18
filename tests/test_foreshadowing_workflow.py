from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication, QObject, Signal

from core.foreshadowing import ForeshadowingStore
from core.expansion import ExpansionRunResult
from core.prose_supplement import ProseSupplementRunResult
from core.project import NovelProject
from ui.ai_result_coordinator import CommitOutcome
from ui.ai_workflow_controller import (
    AIWorkflowController,
    PendingForeshadowingResolution,
)
from ui.project_session import ProjectSession


class FakeAIController(QObject):
    succeeded = Signal(object, object)
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.task_context = {}

    def result_context(self, _token):
        return self.task_context


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

    def test_plain_text_fallback_diagnostic_reports_session_count(self) -> None:
        session = ProjectSession()
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
        messages = []
        controller.output_requested.connect(messages.append)

        controller._record_plain_text_fallbacks(1)
        controller._record_plain_text_fallbacks(2)

        self.assertIn("本会话累计 1 次", messages[0])
        self.assertIn("本会话累计 3 次", messages[1])

    def test_expansion_completion_uses_task_target_snapshot_not_live_setting(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            session.set_project(project)
            ai_controller = FakeAIController()
            ai_controller.task_context = {
                "target_chars": 3000,
                "selected_foreshadowing": (),
            }
            editor = Mock()
            document_controller = FakeDocumentController()
            controller = AIWorkflowController(
                config={"chapter_target_chars": 5000},
                project_session=session,
                editor=editor,
                document_controller=document_controller,
                ai_controller=ai_controller,
                ai_engine_controller=object(),
                inspector=object(),
                reports_page=object(),
                go_to_writing=lambda: True,
            )
            parsed = SimpleNamespace(
                text="正文",
                char_count=2900,
                length_ok=True,
                completion_message="扩写任务已完成",
                foreshadowing_feedback=(),
                feedback_warning="",
            )
            controller.ai_result_service.parse_expansion = Mock(return_value=parsed)
            controller.ai_result_coordinator.confirm_expansion = Mock(
                return_value=CommitOutcome(status="cancelled")
            )
            result = ExpansionRunResult(
                raw_output="raw",
                first_raw_output=None,
                plain_text_fallback_count=0,
                target_chars=3000,
                min_chars=2850,
                max_chars=3150,
                initial_char_count=2000,
                final_char_count=2900,
                supplement_attempted=True,
                supplement_applied=True,
                supplement_added_chars=900,
            )

            controller._on_expansion_done(
                SimpleNamespace(chapter_id="chapter_01"),
                result,
            )

            controller.ai_result_service.parse_expansion.assert_called_once_with(
                "raw",
                3000,
                chapter_id="chapter_01",
                selected_foreshadowing=(),
            )
            confirm_kwargs = (
                controller.ai_result_coordinator.confirm_expansion.call_args.kwargs
            )
            self.assertEqual(confirm_kwargs["target_chars"], 3000)
            self.assertEqual(confirm_kwargs["min_chars"], 2850)
            self.assertEqual(confirm_kwargs["max_chars"], 3150)

    def test_manual_continuation_supplement_reopens_preview_with_updated_counts(self) -> None:
        session = ProjectSession()
        ai_controller = FakeAIController()
        ai_controller.task_context = {
            "writing_kind": "continuation",
            "current_chars": 800,
            "requested_chars": 2200,
            "target_chapter_chars": 3000,
            "run_target_chars": 3000,
            "initial_generated_chars": 1500,
            "supplement_added_total": 200,
            "supplement_attempt_count": 1,
            "current_tail": "原文结尾",
        }
        controller = AIWorkflowController(
            config={},
            project_session=session,
            editor=Mock(),
            document_controller=FakeDocumentController(),
            ai_controller=ai_controller,
            ai_engine_controller=object(),
            inspector=object(),
            reports_page=object(),
            go_to_writing=lambda: True,
        )
        controller.ai_result_coordinator.confirm_continuation = Mock(
            return_value=CommitOutcome(status="cancelled")
        )
        result = ProseSupplementRunResult(
            text="续" * 2100,
            initial_char_count=1700,
            final_char_count=2100,
            added_char_count=400,
            applied=True,
        )

        controller._on_writing_supplement_done(
            SimpleNamespace(chapter_id="chapter_01"),
            result,
        )

        confirm_kwargs = (
            controller.ai_result_coordinator.confirm_continuation.call_args.kwargs
        )
        self.assertEqual(confirm_kwargs["generated_chars"], 2100)
        self.assertEqual(confirm_kwargs["supplement_added_chars"], 600)
        self.assertEqual(confirm_kwargs["supplement_attempt_count"], 2)
        self.assertEqual(confirm_kwargs["length_status"], "qualified")
