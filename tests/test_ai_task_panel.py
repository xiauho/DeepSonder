import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, SkipTest
from unittest.mock import Mock, patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QCloseEvent, QFontDatabase
from PySide6.QtWidgets import QApplication
from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from core.task_controller import AITaskToken
from ui.main_window import MainWindow


class AITaskPanelTests(TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        if existing and not isinstance(existing, QApplication):
            raise SkipTest('Requires QApplication')
        cls.app = existing or QApplication([])
        cls.font_id = -1
        if Path('C:/Windows/Fonts/msyh.ttc').exists():
            cls.font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        restore = patch.object(MainWindow, '_restore_last_project')
        restore.start()
        self.addCleanup(restore.stop)
        self.window = MainWindow(config={**DEFAULT_CONFIG, 'auto_save': False})
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.ai_workflow_controller.discard_pending_result)
        self.project = NovelProject.create(Path(self.tmp.name) / 'project', '任务面板测试')
        self.window.project_session.set_project(self.project)
        self.window.window_state_controller.activate_route('writing')
        self.window.editor.open_file('章节', str(self.project.chapters_dir / 'chapter_01.md'))
        self.window.resize(1100, 720)
        self.window.show()
        self.app.processEvents()
        self.token = AITaskToken('expand', 'chapter_01')

    def test_pending_result_does_not_interrupt_and_survives_worker_finish(self):
        w = self.window
        w.task_panel.start(self.token)
        w.window_state_controller.show_output()
        w.toggle_output()
        w.ai_workflow_controller._on_expansion_done = Mock()
        w.ai_workflow_controller._on_task_succeeded(self.token, 'candidate')
        w.ai_controller.finished.emit(self.token)
        self.app.processEvents()
        self.assertFalse(w.task_panel.isVisible())
        self.assertEqual(w.task_panel.state, 'pending')
        self.assertIn('待审阅', w.task_entry.text())
        for key in ('expand', 'continuation', 'memory', 'check'):
            self.assertFalse(w.actions[key].isEnabled())
        w.task_entry.click()
        self.assertTrue(w.task_panel.isVisible())
        w.task_panel.review_button.click()
        w.ai_workflow_controller._on_expansion_done.assert_called_once_with(self.token, 'candidate')
        self.assertEqual(w.task_panel.state, 'reviewed')
        self.assertFalse(w.ai_workflow_controller.has_pending_result)

    def test_pending_result_blocks_close_and_project_switch(self):
        w = self.window
        w.ai_workflow_controller._on_task_succeeded(self.token, 'candidate')
        with patch('ui.main_window.QMessageBox.information') as message:
            event = QCloseEvent()
            w.closeEvent(event)
            self.assertFalse(event.isAccepted())
            self.assertFalse(w._load_project(self.project.root))
            self.assertEqual(message.call_count, 2)
        self.assertTrue(w.ai_workflow_controller.has_pending_result)
        w.task_panel.discard_button.click()
        self.assertEqual(w.task_panel.state, 'discarded')
        self.assertTrue(w._can_leave_ai_review())

    def test_failure_and_cancellation_are_visible_without_modal_popup(self):
        w = self.window
        with patch('ui.ai_task_view_controller.QMessageBox.critical') as popup:
            w.ai_task_view_controller._on_started(self.token)
            w.ai_task_view_controller._on_failed(self.token, '网络连接失败')
            w.ai_task_view_controller._on_finished(self.token)
            self.assertEqual(w.task_panel.state, 'failed')
            self.assertIn('网络连接失败', w.task_panel.detail.text())
            popup.assert_not_called()
        w.ai_task_view_controller._on_started(self.token)
        with patch.object(w.ai_controller, 'cancel', return_value=True):
            w.task_panel.cancel_button.click()
        self.assertEqual(w.task_panel.state, 'cancelling')
        self.assertFalse(w.task_panel.cancel_button.isEnabled())
        w.ai_task_view_controller._on_cancelled(self.token)
        w.ai_task_view_controller._on_finished(self.token)
        self.assertEqual(w.task_panel.state, 'cancelled')

    def test_focus_mode_keeps_task_results_hidden_until_exit(self):
        w = self.window
        w.window_state_controller.toggle_focus_mode()
        w.ai_task_view_controller._on_started(self.token)
        w.ai_workflow_controller._on_task_succeeded(self.token, 'candidate')
        w.ai_task_view_controller._on_finished(self.token)
        self.assertFalse(w.task_panel.isVisible())
        w.window_state_controller.exit_focus_mode()
        self.assertTrue(w.task_panel.isVisible())
        self.assertTrue(w.task_panel.review_button.isVisible())

    def test_logs_expand_and_clear_without_discarding_candidate(self):
        w = self.window
        w.window_state_controller.show_output()
        w.ai_task_view_controller.append_output('任务日志')
        w.ai_workflow_controller._on_task_succeeded(self.token, 'candidate')
        self.assertFalse(w.task_panel.log_container.isVisible())
        w.task_panel.log_toggle.click()
        self.assertTrue(w.task_panel.log_container.isVisible())
        w.task_panel.clear_requested.emit()
        self.assertEqual(w.output_panel.toPlainText(), '')
        self.assertTrue(w.ai_workflow_controller.has_pending_result)

    def test_long_task_target_does_not_expand_window(self):
        self.window.window_state_controller.show_output()
        self.window.task_panel.start(AITaskToken('expand', 'chapter_' + 'long_name' * 40))
        self.app.processEvents()
        self.assertEqual(self.window.width(), 1100)
        self.assertIn('long_name', self.window.task_panel.scope.toolTip())


    def test_memory_progress_is_queued_and_ignores_old_or_cancelled_runs(self):
        import threading
        from core.memory_progress import MemoryProgress
        w = self.window
        token = AITaskToken('memory', 'chapter_01')
        w.ai_task_view_controller._on_started(token)
        controller = w.ai_workflow_controller
        run_id = object()
        controller._memory_run_id = run_id
        event = MemoryProgress('facts', current=2, total=4)
        # Emit from a real background thread: no widget should change there.
        worker = threading.Thread(target=lambda: controller.memory_progress_received.emit(run_id, token.cancel_event, event))
        worker.start()
        worker.join()
        self.assertNotIn('提取事实 2/4', w.task_panel.detail.text())
        self.app.processEvents()
        self.assertIn('提取事实 2/4', w.task_panel.detail.text())
        with patch('ui.ai_task_view_controller.time.monotonic', return_value=w.ai_task_view_controller._task_started_at + 12):
            w.ai_task_view_controller._refresh_elapsed()
        self.assertIn('已用 12 秒', w.task_panel.detail.text())
        controller._on_memory_progress(object(), token.cancel_event, MemoryProgress('proposal'))
        self.assertIn('提取事实 2/4', w.task_panel.detail.text())
        token.cancel_event.set()
        w.task_panel.set_state('cancelling', '正在取消')
        controller._on_memory_progress(run_id, token.cancel_event, MemoryProgress('proposal'))
        w.ai_task_view_controller._refresh_elapsed()
        self.assertEqual(w.task_panel.detail.text(), '正在取消')
        controller._on_memory_finished(token)
        self.assertIsNone(controller._memory_run_id)
        w.ai_task_view_controller._on_finished(token)
        self.assertFalse(w.ai_task_view_controller._elapsed_timer.isActive())

    def test_memory_progress_wraps_at_narrow_width_in_both_themes(self):
        from ui.theme import apply_theme
        from core.memory_progress import MemoryProgress
        token = AITaskToken('memory', 'chapter_01')
        w = self.window
        w.ai_task_view_controller._on_started(token)
        controller = w.ai_workflow_controller
        run_id = object()
        controller._memory_run_id = run_id
        try:
            for theme in ('light', 'dark'):
                for font_size in (14, 20):
                    with self.subTest(theme=theme, font_size=font_size):
                        apply_theme(self.app, {'theme': theme, 'ui_font_size': font_size})
                        w.resize(1100, 760)
                        controller._on_memory_progress(run_id, token.cancel_event,
                            MemoryProgress('facts', current=12, total=24))
                        w.ai_task_view_controller._refresh_elapsed()
                        self.app.processEvents()
                        self.assertTrue(w.task_panel.detail.wordWrap())
                        self.assertLessEqual(w.task_panel.detail.geometry().right(), w.task_panel.width())
                        self.assertGreaterEqual(w.task_panel.detail.height(),
                            w.task_panel.detail.heightForWidth(w.task_panel.detail.width()))
                        self.assertTrue(w.task_panel.cancel_button.isEnabled())
                        self.assertIn('提取事实 12/24', w.task_panel.detail.text())
        finally:
            controller._on_memory_finished(token)
            w.ai_task_view_controller._on_finished(token)
            apply_theme(self.app, DEFAULT_CONFIG)
