import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, SkipTest
from unittest.mock import Mock, patch
from PySide6.QtCore import QCoreApplication, Qt, QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog
from core.project import NovelProject
from core.prose_review import ProsePatch, ProseReview
from core.task_controller import AITaskToken
from ui.editor import Editor
from ui.ai_workflow_controller import AIWorkflowController
from ui.prose_review_dialog import ProseReviewDialog, ProseRequestDialog
from ui.project_session import ProjectSession

class FakeAI(QObject):
    succeeded=Signal(object,object)
    finished=Signal(object)
    def __init__(self):
        super().__init__()
        self.release_result=Mock()
        self.context_matches=Mock(return_value=True)
    def result_context(self, token): return None

class FakeDocument(QObject):
    document_saved=Signal(str)

class ProseReviewUITests(TestCase):
    @classmethod
    def setUpClass(cls):
        instance=QCoreApplication.instance()
        if instance is not None and not isinstance(instance,QApplication):
            raise SkipTest('Requires QApplication')
        cls.app=instance or QApplication([])

    def setUp(self):
        self.tmp=TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.project=NovelProject.create(Path(self.tmp.name)/'novel','测试')
        self.editor=Editor(); self.addCleanup(self.editor.deleteLater)
        self.source='# 第一章\n\n## 正文\n😀他把门推开。\n她站在窗边。\n'
        self.editor.text_edit.setPlainText(self.source)
        offset=self.source.index('他把门推开。')
        self.proposal=ProsePatch(offset,offset+len('他把门推开。'),'他把门推开。','他推开门。','减少冗余')
        self.result=ProseReview(self.source,(self.proposal,),(),'style_review',offset,len(self.source))
        self.session=ProjectSession(); self.session.set_project(self.project)
        self.ai=FakeAI()
        self.workflow=AIWorkflowController(config={},project_session=self.session,editor=self.editor,
            document_controller=FakeDocument(),ai_controller=self.ai,ai_engine_controller=Mock(),
            inspector=Mock(),reports_page=Mock(),go_to_writing=lambda:True,defer_result_review=True)
        self.workflow._task_context_matches=Mock(return_value=True)
        self.token=AITaskToken('style_review','chapter_01')

    def dialog(self,accepted=True,exceptions=()):
        return SimpleNamespace(exec=Mock(return_value=QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected),
            selected_patches=lambda:(self.proposal,) if not exceptions else (), exceptions=set(exceptions))

    def test_selected_task_captures_unicode_range_and_one_time_request(self):
        from PySide6.QtGui import QTextCursor
        p = self.proposal
        cursor = self.editor.text_edit.textCursor()
        cursor.setPosition(len(self.source[:p.start].encode("utf-16-le")) // 2)
        cursor.setPosition(len(self.source[:p.end].encode("utf-16-le")) // 2, QTextCursor.MoveMode.KeepAnchor)
        self.editor.text_edit.setTextCursor(cursor)
        service = Mock()
        self.workflow._prepare_request = Mock(return_value=(self.project, "chapter_01", service))
        self.editor.current_chapter_id = Mock(return_value="chapter_01")
        self.workflow._start = Mock()
        dialog = SimpleNamespace(exec=lambda: QDialog.DialogCode.Accepted,
            requirements=SimpleNamespace(toPlainText=lambda: "保留克制语气"))
        with patch("ui.prose_review_dialog.ProseRequestDialog", return_value=dialog):
            self.workflow.prose_task("selection_expand")
        call = self.workflow._start.call_args
        self.assertEqual(call.kwargs["task_context"]["start"], p.start)
        self.assertEqual(call.kwargs["task_context"]["end"], p.end)
        self.assertEqual(call.kwargs["task_context"]["request"], "保留克制语气")
        call.args[3](None)
        service.review_prose.assert_called_once()

    def test_selection_outside_body_does_not_start(self):
        from PySide6.QtGui import QTextCursor
        cursor = self.editor.text_edit.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
        self.editor.text_edit.setTextCursor(cursor)
        self.workflow._prepare_request = Mock(return_value=(self.project, "chapter_01", Mock()))
        self.workflow._start = Mock()
        with patch("ui.ai_workflow_controller.QMessageBox.information"):
            self.workflow.prose_task("selection_expand")
        self.workflow._start.assert_not_called()

    def test_deferred_prose_review_retains_snapshot_until_user_opens(self):
        self.workflow._on_prose_done=Mock()
        self.ai.succeeded.emit(self.token,self.result)
        self.workflow._on_prose_done.assert_not_called()
        self.ai.release_result.assert_not_called()
        self.workflow.review_pending_result()
        self.workflow._on_prose_done.assert_called_once_with(self.token,self.result)
        self.ai.release_result.assert_called_once_with(self.token)

    def test_cancel_preserves_text_and_exceptions(self):
        with patch('ui.prose_review_dialog.ProseReviewDialog',return_value=self.dialog(False, (0,))):
            self.workflow._on_prose_done(self.token,self.result)
        self.assertEqual(self.editor.text_edit.toPlainText(),self.source)
        self.assertFalse((self.project.root/'writing/style_exceptions.json').exists())

    def test_stale_after_dialog_preserves_text_and_exceptions(self):
        self.workflow._task_context_matches.side_effect=[True,False]
        with patch('ui.prose_review_dialog.ProseReviewDialog',return_value=self.dialog(True,(0,))), patch('ui.ai_workflow_controller.QMessageBox.warning'):
            self.workflow._on_prose_done(self.token,self.result)
        self.assertEqual(self.editor.text_edit.toPlainText(),self.source)
        self.assertFalse((self.project.root/'writing/style_exceptions.json').exists())

    def test_confirmed_unicode_patch_is_undoable(self):
        with patch('ui.prose_review_dialog.ProseReviewDialog',return_value=self.dialog()):
            self.workflow._on_prose_done(self.token,self.result)
        self.assertEqual(self.editor.text_edit.toPlainText(),self.source.replace('他把门推开。','他推开门。'))
        self.editor.undo()
        self.assertEqual(self.editor.text_edit.toPlainText(),self.source)

    def test_direct_unicode_range_replacement_preserves_surroundings(self):
        p=self.proposal
        self.assertTrue(self.editor.replace_range_if_matches(p.start,p.end,p.expected_original,p.replacement))
        self.assertEqual(self.editor.text_edit.toPlainText(),self.source.replace(p.expected_original,p.replacement))

    def test_whitelist_requires_confirmation_and_preserves_original(self):
        with patch('ui.prose_review_dialog.ProseReviewDialog',return_value=self.dialog(True,(0,))):
            self.workflow._on_prose_done(self.token,self.result)
        from core.writing_style import load_exceptions
        self.assertEqual(load_exceptions(self.project.root),('他把门推开。',))
        self.assertEqual(self.editor.text_edit.toPlainText(),self.source)

    def test_dialog_starts_unselected_and_exceptions_are_exclusive(self):
        dialog=ProseReviewDialog(self.result)
        self.addCleanup(dialog.deleteLater)
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.items.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog._toggle_exception()
        self.assertFalse(dialog.selected_patches())
        self.assertEqual(dialog.exceptions,{0})
        dialog.items.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertFalse(dialog.exceptions)

    def test_narrow_dialog_light_dark_and_large_font(self):
        from core.config import DEFAULT_CONFIG
        from ui.theme import apply_theme
        for theme in ('light','dark'):
            apply_theme(self.app,{**DEFAULT_CONFIG,'theme':theme})
            dialog=ProseReviewDialog(self.result)
            font=dialog.font(); font.setPointSize(16); dialog.setFont(font)
            dialog.resize(720,640); dialog.show(); self.app.processEvents()
            for widget in (dialog.items,dialog.preview,dialog.findings,dialog.apply_button):
                self.assertTrue(widget.isVisible())
                self.assertGreater(widget.width(),100)
                self.assertGreater(widget.height(),15)
                point=widget.mapTo(dialog,widget.rect().bottomRight())
                self.assertLessEqual(point.x(),dialog.width())
                self.assertLessEqual(point.y(),dialog.height())
            dialog.close(); dialog.deleteLater()

    def test_main_menu_selection_and_pending_result_availability(self):
        from core.config import DEFAULT_CONFIG
        from ui.main_window import MainWindow
        from PySide6.QtGui import QTextCursor
        self.project.chapters_dir.joinpath("chapter_01.md").write_text(self.source, encoding="utf-8")
        with patch.object(MainWindow, "_restore_last_project"):
            window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False})
        window.project_lifecycle_controller.persist_config = lambda _config: None
        try:
            window.project_session.set_project(self.project)
            window.editor.open_file("章节", str(self.project.chapters_dir / "chapter_01.md"))
            window._refresh_ai_actions()
            self.assertEqual(window.actions["expand"].text(), "按章纲生成正文")
            self.assertFalse(window.actions["selection_expand"].isEnabled())
            self.assertTrue(window.actions["style_review"].isEnabled())
            cursor = window.editor.text_edit.textCursor()
            start, end = self.proposal.start, self.proposal.end
            cursor.setPosition(len(self.source[:start].encode("utf-16-le")) // 2)
            cursor.setPosition(len(self.source[:end].encode("utf-16-le")) // 2, QTextCursor.MoveMode.KeepAnchor)
            window.editor.text_edit.setTextCursor(cursor)
            self.assertTrue(window.actions["selection_expand"].isEnabled())
            self.assertTrue(window.actions["style_polish"].isEnabled())
            window.ai_workflow_controller._pending_review = (self.token, self.result)
            window._refresh_ai_actions()
            for key in ("selection_expand", "style_polish", "style_review", "style_exceptions"):
                self.assertFalse(window.actions[key].isEnabled())
        finally:
            window.ai_workflow_controller._pending_review = None
            window.close()
            window.deleteLater()

    def test_request_limit_keeps_dialog_open_with_error(self):
        dialog=ProseRequestDialog('style_review',30)
        dialog.requirements.setPlainText('字'*2001)
        dialog._submit()
        self.assertTrue(dialog.error.text())
        self.assertNotEqual(dialog.result(),QDialog.DialogCode.Accepted)
        dialog.deleteLater()
