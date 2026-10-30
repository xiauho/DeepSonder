"""Use actual embedded controls; every decision must pass the existing commit guards."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QTimer, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from core.config import DEFAULT_CONFIG
from core.expansion import ExpansionRunResult
from core.task_controller import AITaskToken
from ui.ai_review_panel import review_diff
from ui.main_window import MainWindow


class AIReviewPanelTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = Path("C:/Windows/Fonts/msyh.ttc")
        cls.font_id = QFontDatabase.addApplicationFont(str(font)) if font.exists() else -1

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        from core.project import NovelProject
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        restore = patch.object(MainWindow, "_restore_last_project")
        restore.start()
        self.addCleanup(restore.stop)
        self.project = NovelProject.create(Path(self.tmp.name) / "book", "侧栏审阅测试")
        self.project.save_chapter("chapter_01", "先到旧站", "原正文，保留这句话。", title="第一章")
        self.path = self.project.chapters_dir / "chapter_01.md"
        self.original = self.path.read_bytes()
        self.window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False}, ui_state_path=Path(self.tmp.name) / "ui.json")
        self.addCleanup(self.close_window)
        self.window.resize(1600, 900)
        self.window.show()
        self.window.project_session.set_project(self.project)
        self.window._show_route("writing")
        QTest.qWait(50)
        self.pane = self.window.review_panel
        self.coordinator = self.window.ai_workflow_controller.ai_result_coordinator
        self.errors = []

    def close_window(self):
        self.window.ai_workflow_controller.discard_pending_result()
        self.window.editor._set_dirty(False)
        self.window.review_panel.finish()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def later(self, callback, delay=50):
        def inspect():
            try:
                callback()
            except BaseException as exc:
                self.errors.append(exc)
                self.pane.discard()
        QTimer.singleShot(delay, self.window, inspect)

    def run_review(self, callback, execute):
        self.errors.clear()
        watchdog = QTimer(self.window)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(lambda: (self.errors.append(AssertionError("review did not finish")), self.pane.discard()))
        watchdog.start(5000)
        self.later(callback)
        try:
            value = execute()
        finally:
            watchdog.stop()
            watchdog.deleteLater()
        if self.errors:
            raise self.errors[0]
        return value

    def confirm_button(self):
        return next(b for b in self.pane.footer.findChildren(QPushButton)
                    if b.property("reviewNeedsContext"))

    def begin(self):
        self.pane.begin("扩写", "chapter_01")

    def test_expansion_candidate_diff_postpone_and_explicit_commit(self):
        self.begin()
        replace = Mock()
        def inspect():
            self.assertIsNone(QApplication.activeModalWidget())
            dialog = self.pane.dialog
            selector = dialog.findChild(QComboBox)
            dialog._foreshadowing_selectors["f-one"].setCurrentIndex(1)
            selector.setCurrentIndex(1)
            self.assertEqual(dialog.selected_resolution_ids(), ())
            self.pane.tabs.setCurrentIndex(1)
            self.assertIn("+首次原稿", self.pane.diff.toPlainText())
            self.assertTrue(self.window.page_stack.isVisible())
            self.pane.postpone()
            self.assertFalse(self.pane.isVisible())
            self.assertTrue(self.pane.active)
            replace.assert_not_called()
            self.pane.resume()
            self.assertIs(self.pane.dialog, dialog)
            self.assertEqual(selector.currentIndex(), 1)
            self.assertEqual(dialog._foreshadowing_selectors["f-one"].currentIndex(), 1)
            self.assertTrue(self.pane.footer.isVisible())
            self.confirm_button().click()
        outcome = self.run_review(inspect, lambda: self.coordinator.confirm_expansion(
            text="推荐稿", original_text="旧正文", char_count=3, length_ok=True,
            has_existing_content=True, original_draft_text="首次原稿",
            foreshadowing_titles={"f-one": "旧站仍亮着的灯"},
            context_matches=lambda: True, replace_body=replace))
        self.assertEqual(outcome.status, "committed")
        self.assertEqual(outcome.value, ())
        replace.assert_called_once_with("首次原稿")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_stale_sources_disable_write_but_keep_candidate_visible(self):
        self.begin()
        replace = Mock()
        fresh = [True]
        def inspect():
            fresh[0] = False
            self.pane.check_sources()
            self.assertTrue(self.pane.stale)
            self.assertIn("来源已变化", self.pane.source_status.text())
            button = self.confirm_button()
            self.assertFalse(button.isEnabled())
            button.setEnabled(True)
            self.assertFalse(button.isEnabled())
            self.assertIn("推荐稿", self.pane.dialog._candidate_editor.toPlainText())
            next(b for b in self.pane.footer.findChildren(QPushButton) if b.property("reviewDiscard")).click()
        outcome = self.run_review(inspect, lambda: self.coordinator.confirm_expansion(
            text="推荐稿", char_count=3, length_ok=True, has_existing_content=True,
            context_matches=lambda: fresh[0], replace_body=replace))
        self.assertEqual(outcome.status, "cancelled")
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_narrow_review_uses_full_page_and_discard_unblocks_suspended_review(self):
        self.window.resize(1100, 720)
        self.begin()
        commit = Mock()
        def inspect():
            self.assertFalse(self.window.page_stack.isVisible())
            self.assertEqual(self.pane.back_button.text(), "返回正文")
            self.assertEqual(self.window.width(), 1100)
            self.pane.postpone()
            self.assertTrue(self.window.page_stack.isVisible())
            self.assertTrue(self.pane.active)
            self.pane.discard()
        outcome = self.run_review(inspect, lambda: self.coordinator.confirm_memory(
            summary="本章摘要", details="状态变更：无", context_matches=lambda: True, commit=commit))
        self.assertEqual(outcome.status, "cancelled")
        commit.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_deferred_workflow_keeps_snapshot_and_pending_guards_until_decision(self):
        w = self.window
        workflow = w.ai_workflow_controller
        workflow._task_context_matches = Mock(return_value=True)
        release = Mock(wraps=w.ai_controller.release_result)
        w.ai_controller.release_result = release
        token = AITaskToken("expand", "chapter_01")
        candidate = "灯" * 300
        result = ExpansionRunResult(raw_output=f"<NOVEL_TEXT>{candidate}</NOVEL_TEXT>", first_raw_output=None,
            plain_text_fallback_count=0, target_chars=300, min_chars=240, max_chars=360,
            initial_char_count=300, final_char_count=300)
        workflow._on_task_succeeded(token, result)
        def inspect():
            self.assertTrue(workflow.has_pending_result)
            release.assert_not_called()
            self.pane.postpone()
            self.assertEqual(w.task_panel.state, "pending")
            self.assertTrue(w.actions["review_result"].isEnabled())
            with patch("ui.main_window.QMessageBox.information"):
                self.assertFalse(w._can_leave_ai_review())
            self.assertFalse(workflow._start("memory", "chapter_01", "", Mock()))
            w._review_ai_result()
            self.assertEqual(w.task_panel.state, "reviewing")
            self.confirm_button().click()
        self.run_review(inspect, w._review_ai_result)
        release.assert_called_once_with(token)
        self.assertFalse(workflow.has_pending_result)
        self.assertFalse(self.pane.active)
        self.assertTrue(w.editor.is_dirty())
        self.assertEqual(w.editor.writing_page.body_edit.toPlainText(), candidate)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_display_diff_limits_do_not_change_inputs(self):
        original, proposed = "旧正文。", "新正文。"
        self.assertIn("-旧正文。", review_diff(original, proposed))
        self.assertIn("+新正文。", review_diff(original, proposed))
        self.assertIn("内容较长", review_diff("原" * 100_001, "新" * 100_001))

    def test_prose_selection_exceptions_and_diff_use_the_existing_commit_flow(self):
        from core.prose_review import ProsePatch, ProseReview
        from core.writing_style import load_exceptions
        w = self.window
        workflow = w.ai_workflow_controller
        workflow._task_context_matches = Mock(return_value=True)
        source = w.editor.document_text()
        first, second = source.index("原正文"), source.index("保留这句话")
        proposals = (
            ProsePatch(first, first + 3, "原正文", "新的正文", "明确表达"),
            ProsePatch(second, second + 5, "保留这句话", "不要采用此项", "示例建议"),
        )
        review = ProseReview(source, proposals, (), "style_review", first, second + 5)
        token = AITaskToken("style_review", "chapter_01")
        workflow._on_task_succeeded(token, review)
        def inspect():
            dialog = self.pane.dialog
            self.assertFalse(dialog.apply_button.isEnabled())
            dialog.items.item(0).setCheckState(Qt.CheckState.Checked)
            dialog.items.setCurrentRow(1)
            dialog.keep.click()
            self.assertEqual(dialog.exceptions, {1})
            self.pane.postpone()
            self.pane.resume()
            self.assertIs(self.pane.dialog, dialog)
            self.assertEqual(dialog.exceptions, {1})
            self.assertEqual(len(dialog.selected_patches()), 1)
            self.pane.tabs.setCurrentIndex(1)
            self.assertIn("新的正文", self.pane.diff.toPlainText())
            self.assertNotIn("不要采用此项", self.pane.diff.toPlainText())
            self.assertFalse(load_exceptions(self.project.root))
            self.assertEqual(self.path.read_bytes(), self.original)
            dialog.apply_button.click()
        self.run_review(inspect, w._review_ai_result)
        self.assertIn("新的正文，保留这句话。", w.editor.document_text())
        self.assertIn("保留这句话", load_exceptions(self.project.root))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_blocked_memory_requires_separate_partial_preview_and_confirmation(self):
        from test_memory_recovery import mixed_proposal
        from PySide6.QtWidgets import QMessageBox
        proposal, state = mixed_proposal()
        w = self.window
        workflow = w.ai_workflow_controller
        workflow._task_context_matches = Mock(return_value=True)
        workflow.ai_result_service.commit_memory_proposal = Mock(return_value=None)
        workflow.ai_result_service.downstream_memory_chapters = Mock(return_value=())
        token = AITaskToken("memory", "chapter_01")
        workflow._on_task_succeeded(token, proposal)
        seen = []
        def inspect_partial():
            dialog = self.pane.dialog
            self.assertNotIsInstance(dialog, QMessageBox)
            self.assertIn("未采用的更新", dialog.preview.toPlainText())
            self.assertIn("原摘要未采用", dialog.preview.toPlainText())
            workflow.ai_result_service.commit_memory_proposal.assert_not_called()
            seen.append("partial")
            self.confirm_button().click()
        def inspect_blocked():
            self.assertIsInstance(self.pane.dialog, QMessageBox)
            workflow.ai_result_service.commit_memory_proposal.assert_not_called()
            self.assertIn("审阅可用部分", [b.text() for b in self.pane.footer.findChildren(QPushButton)],
                          repr([b.text() for b in self.pane.dialog.findChildren(QPushButton)]) + self.pane.dialog.informativeText())
            button = next(b for b in self.pane.footer.findChildren(QPushButton) if b.text() == "审阅可用部分")
            seen.append("blocked")
            self.later(inspect_partial)
            button.click()
        with patch.object(self.project, "load_story_state", return_value=state):
            self.run_review(inspect_blocked, w._review_ai_result)
        self.assertEqual(seen, ["blocked", "partial"])
        committed = workflow.ai_result_service.commit_memory_proposal.call_args.args[2]
        self.assertEqual(len(committed.patches), 1)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_continuation_retry_retains_selected_candidate_without_appending(self):
        self.begin()
        append = Mock()
        def inspect():
            self.pane.dialog.findChild(QComboBox).setCurrentIndex(1)
            button = next(b for b in self.pane.footer.findChildren(QPushButton) if b.text() == "重新补写")
            button.click()
        result = self.run_review(inspect, lambda: self.coordinator.confirm_continuation(
            text="偏短续写", current_tail="当前结尾", current_chars=100, requested_chars=200,
            generated_chars=4, target_chapter_chars=300, length_ok=False, length_status="under",
            original_draft_text="首次续写稿", context_matches=lambda: True, append_body=append))
        self.assertEqual(result.status, "supplement_requested")
        self.assertEqual(result.value, "首次续写稿")
        append.assert_not_called()

    def test_repair_keeps_unique_match_guard_and_original_replacement_diff(self):
        self.begin()
        apply = Mock(return_value=False)
        def inspect():
            self.pane.tabs.setCurrentIndex(1)
            self.assertIn("-原文", self.pane.diff.toPlainText())
            self.assertIn("+修复后", self.pane.diff.toPlainText())
            self.confirm_button().click()
        result = self.run_review(inspect, lambda: self.coordinator.confirm_repair(
            expected_original="原文", replacement="修复后", explanation="修复连续的一处",
            preserved_facts=("保留事实",), context_matches=lambda: True, apply_replacement=apply))
        self.assertEqual(result.status, "failed")
        apply.assert_called_once_with()
        self.assertEqual(self.path.read_bytes(), self.original)

    def start_local_result(self):
        """Use a real captured snapshot and worker lifecycle without a model call."""
        candidate = "灯" * 300
        result = ExpansionRunResult(raw_output=f"<NOVEL_TEXT>{candidate}</NOVEL_TEXT>", first_raw_output=None,
            plain_text_fallback_count=0, target_chars=300, min_chars=240, max_chars=360,
            initial_char_count=300, final_char_count=300)
        token = self.window.ai_controller.start("expand", self.project, "chapter_01",
            self.window.editor.document_text(), lambda _cancel_event: result)
        self.assertIsNotNone(token)
        for _ in range(200):
            if self.window.ai_workflow_controller.has_pending_result and not self.window.ai_controller.is_running():
                break
            QTest.qWait(10)
        self.assertTrue(self.window.ai_workflow_controller.has_pending_result)
        self.assertFalse(self.window.ai_controller.is_running())
        return token

    def test_real_snapshot_blocks_after_editing_while_review_is_postponed(self):
        w = self.window
        self.start_local_result()
        def inspect():
            self.assertFalse(self.pane.stale)
            self.pane.postpone()
            w.editor.writing_page.body_edit.insertPlainText("作者新增。")
            w._review_ai_result()
            self.assertTrue(self.pane.stale)
            self.assertFalse(self.confirm_button().isEnabled())
            self.pane.discard()
        self.run_review(inspect, w._review_ai_result)
        self.assertIn("作者新增。", w.editor.document_text())
        self.assertNotIn("灯" * 300, w.editor.document_text())
        self.assertTrue(w.editor.is_dirty())
        self.assertFalse(w.ai_workflow_controller.has_pending_result)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_real_snapshot_blocks_external_canon_change_without_overwriting_it(self):
        w = self.window
        self.start_local_result()
        world = self.project.list_world()[0]
        external = world.read_text(encoding="utf-8") + "\n外部写入的规则。\n"
        def inspect():
            world.write_text(external, encoding="utf-8")
            self.pane.check_sources()
            self.assertTrue(self.pane.stale)
            self.assertFalse(self.confirm_button().isEnabled())
            self.pane.discard()
        self.run_review(inspect, w._review_ai_result)
        self.assertEqual(world.read_text(encoding="utf-8"), external)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(w.editor.is_dirty())

    def test_escape_and_f6_return_to_source_without_losing_review(self):
        self.window.resize(1100, 720)
        self.begin()
        replace = Mock()
        def inspect():
            dialog = self.pane.dialog
            dialog._candidate_editor.setFocus()
            QTest.keyClick(dialog._candidate_editor, Qt.Key.Key_Escape)
            QTest.qWait(20)
            self.assertFalse(self.pane.isVisible())
            self.assertTrue(self.pane.active)
            self.pane.resume()
            self.window.actions["focus_editor"].trigger()
            self.assertFalse(self.pane.isVisible())
            self.assertIs(self.window.focusWidget(), self.window.editor.writing_page.body_edit)
            self.pane.resume()
            self.assertIs(self.pane.dialog, dialog)
            self.pane.discard()
        result = self.run_review(inspect, lambda: self.coordinator.confirm_expansion(
            text="候选", char_count=2, length_ok=True, has_existing_content=True,
            context_matches=lambda: True, replace_body=replace))
        self.assertEqual(result.status, "cancelled")
        replace.assert_not_called()

    def test_navigation_postpones_review_and_reopening_keeps_its_controls(self):
        self.begin()
        replace = Mock()
        def inspect():
            dialog = self.pane.dialog
            self.window.actions["show_memory"].trigger()
            self.assertFalse(self.pane.isVisible())
            self.assertTrue(self.window.memory_page.isVisible())
            self.assertTrue(self.pane.active)
            self.pane.resume()
            self.assertIs(self.pane.dialog, dialog)
            self.window._on_file_selected("章节", str(self.path))
            self.assertFalse(self.pane.isVisible())
            self.assertEqual(self.window.window_state_controller.current_route, "writing")
            self.assertTrue(self.pane.active)
            self.pane.resume()
            self.pane.discard()
        result = self.run_review(inspect, lambda: self.coordinator.confirm_expansion(
            text="候选", char_count=2, length_ok=True, has_existing_content=True,
            context_matches=lambda: True, replace_body=replace))
        self.assertEqual(result.status, "cancelled")
        replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)
