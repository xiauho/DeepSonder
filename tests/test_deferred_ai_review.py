from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch
from PySide6.QtCore import QCoreApplication, QObject, Signal
from core.task_controller import AITaskToken
from ui.ai_workflow_controller import AIWorkflowController
from ui.project_session import ProjectSession


class FakeAI(QObject):
    succeeded = Signal(object, object)
    def __init__(self):
        super().__init__()
        self.release_result = Mock()
        self.start = Mock()
        self.context_matches = Mock(return_value=True)
    def result_context(self, token):
        return None

class FakeDocument(QObject):
    document_saved = Signal(str)


class DeferredAIReviewTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.ai = FakeAI()
        self.session = ProjectSession()
        self.workflow = AIWorkflowController(
            config={}, project_session=self.session, editor=Mock(), document_controller=FakeDocument(),
            ai_controller=self.ai, ai_engine_controller=Mock(), inspector=Mock(), reports_page=Mock(),
            go_to_writing=lambda: True, defer_result_review=True,
        )
        self.token = AITaskToken('expand', 'chapter_01')

    def test_no_review_or_snapshot_release_before_explicit_request(self):
        handler = Mock()
        self.workflow._on_expansion_done = handler
        result = object()
        self.ai.succeeded.emit(self.token, result)
        handler.assert_not_called()
        self.ai.release_result.assert_not_called()
        self.assertTrue(self.workflow.has_pending_result)
        self.assertFalse(self.workflow._start('memory', 'chapter_01', '', Mock()))
        self.ai.start.assert_not_called()
        self.workflow.review_pending_result()
        handler.assert_called_once_with(self.token, result)
        self.ai.release_result.assert_called_once_with(self.token)
        self.assertFalse(self.workflow.has_pending_result)
        self.workflow.review_pending_result()
        handler.assert_called_once()

    def test_discard_releases_without_invoking_result_handler(self):
        self.workflow._on_expansion_done = Mock()
        self.ai.succeeded.emit(self.token, object())
        self.workflow.discard_pending_result()
        self.workflow._on_expansion_done.assert_not_called()
        self.ai.release_result.assert_called_once_with(self.token)
        self.assertFalse(self.workflow.has_pending_result)

    def test_project_reset_releases_pending_snapshot(self):
        self.ai.succeeded.emit(self.token, object())
        self.session.project_changed.emit(None)
        self.ai.release_result.assert_called_once_with(self.token)
        self.assertFalse(self.workflow.has_pending_result)

    def test_stale_delayed_check_does_not_publish_report(self):
        token = AITaskToken('check', 'chapter_01')
        self.workflow._task_context_matches = Mock(return_value=False)
        self.ai.succeeded.emit(token, 'stale report')
        with patch('ui.ai_workflow_controller.QMessageBox.warning'):
            self.workflow.review_pending_result()
        self.workflow.reports_page.show_result.assert_not_called()
        self.ai.release_result.assert_called_once_with(token)

    def test_handler_failure_releases_snapshot_and_unlocks_actions(self):
        self.workflow._on_expansion_done = Mock(side_effect=ValueError('invalid result'))
        self.ai.succeeded.emit(self.token, object())
        with self.assertRaises(ValueError):
            self.workflow.review_pending_result()
        self.ai.release_result.assert_called_once_with(self.token)
        self.assertFalse(self.workflow.has_pending_result)

    def test_nested_review_cannot_start_another_task(self):
        def handle(*_args):
            self.assertTrue(self.workflow.has_pending_result)
            self.assertFalse(self.workflow._start('memory', 'chapter_01', '', Mock()))
        self.workflow._on_expansion_done = handle
        self.ai.succeeded.emit(self.token, object())
        self.workflow.review_pending_result()
        self.ai.start.assert_not_called()

    def test_delayed_expansion_still_checks_context_before_replacing_text(self):
        from core.expansion import ExpansionRunResult
        result = ExpansionRunResult(
            raw_output="<NOVEL_TEXT>正文</NOVEL_TEXT>", first_raw_output=None,
            plain_text_fallback_count=0, target_chars=2, min_chars=1,
            max_chars=3, initial_char_count=2, final_char_count=2,
        )
        parsed = SimpleNamespace(text="正文", char_count=2, length_ok=True,
                                 feedback_warning="", completion_message="生成完成",
                                 foreshadowing_feedback=())
        self.workflow.ai_result_service.parse_expansion = Mock(return_value=parsed)
        self.workflow._task_context_matches = Mock(return_value=False)
        self.ai.succeeded.emit(self.token, result)
        self.workflow.editor.replace_chapter_body.assert_not_called()
        dialog = SimpleNamespace(confirmed=True, retry_requested=False, exec=Mock())
        with patch('ui.ai_result_coordinator.ExpansionPreviewDialog', return_value=dialog), patch('ui.ai_result_coordinator.QMessageBox.warning'):
            self.workflow.review_pending_result()
        self.workflow.editor.replace_chapter_body.assert_not_called()
        self.ai.release_result.assert_called_once_with(self.token)
