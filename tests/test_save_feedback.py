"""Unified feedback must follow real writes and the visible document owner."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from core.storage import atomic_write_text
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from tests.test_canon_schema import character_text
from ui.main_window import MainWindow


class SaveFeedbackTests(TestCase):
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
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        restore = patch.object(MainWindow, "_restore_last_project")
        restore.start()
        self.addCleanup(restore.stop)
        self.project = NovelProject.create(Path(self.tmp.name) / "book", "保存反馈")
        self.project.save_chapter("chapter_01", "", "正文。", title="第一章")
        self.project.save_chapter("chapter_02", "", "第二章正文。", title="第二章")
        self.path = self.project.chapters_dir / "chapter_01.md"
        self.second = self.project.chapters_dir / "chapter_02.md"
        self.card = self.project.canon_dir / "characters" / "白鸥.md"
        self.card.write_bytes(character_text().replace("\n", "\r\n").encode("utf-8"))
        self.original = self.path.read_bytes()
        self.window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False},
                                 ui_state_path=Path(self.tmp.name) / "ui.json")
        self.addCleanup(self.close_window)
        self.window.resize(1440, 900)
        self.window.show()
        self.window.project_session.set_project(self.project)
        self.window._show_route("writing")
        self.window._on_file_selected("章节", str(self.path))
        self.settle()

    def settle(self):
        self.app.processEvents()

    def close_window(self):
        w = self.window
        w.editor._set_dirty(False)
        w.structured_canon_page.reset()
        w.settings_page.set_config(w.config)
        w.close()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def body(self):
        return self.window.editor.writing_page.body_edit

    def canon(self):
        self.assertTrue(self.window._open_canon_page(str(self.card)))
        self.settle()
        return self.window.structured_canon_page

    def name_field(self, page):
        return next(e for _s, key, e in page.form._field_widgets if key == "姓名")

    def assert_state(self, state, label):
        self.assertEqual(self.window.save_status.property("saveState"), state)
        self.assertEqual(self.window.save_status.text(), label)

    def test_chapter_clean_dirty_saving_and_real_save_share_one_status(self):
        w = self.window
        self.assert_state("clean", "已保存")
        self.body().insertPlainText("草稿。")
        self.assert_state("dirty", "未保存")
        self.assertTrue(w.editor.dirty_badge.isHidden())
        service = w.document_controller.document_service
        original_save = service.save_document
        def save(*args, **kwargs):
            self.assert_state("saving", "正在保存…")
            self.assertEqual(self.path.read_bytes(), self.original)
            return original_save(*args, **kwargs)
        with patch.object(service, "save_document", side_effect=save):
            self.assertTrue(w.save_current_file())
        self.assert_state("clean", "已保存")
        self.assertIn("草稿。", self.path.read_text(encoding="utf-8"))

    def test_failure_survives_typing_ai_progress_settings_and_failed_retry(self):
        w = self.window
        self.body().insertPlainText("保留草稿。")
        with patch.object(w.document_controller.document_service, "save_document", side_effect=OSError("磁盘只读")):
            self.assertFalse(w.document_controller.auto_save())
            self.assert_state("error", "保存失败")
            self.body().insertPlainText("继续写。")
            w.ai_task_view_controller.set_status("AI 正在生成结果")
            w.config["auto_save"] = True
            w._refresh_auto_save_timer()
            w.editor.retry_save_button.click()
        self.assert_state("error", "保存失败")
        self.assertIn("磁盘只读", w.editor.path_label.text())
        self.assertTrue(w.editor.save_recovery_bar.isVisible())
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertIn("自动保存已开启", w.save_status.toolTip())
        self.assertTrue(w.document_controller.save())
        self.assert_state("clean", "已保存")
        self.assertTrue(w.editor.save_recovery_bar.isHidden())
        self.assertTrue(w.editor.path_label.isHidden())

    def test_chapter_conflict_retry_requires_explicit_overwrite_choice(self):
        w = self.window
        self.body().insertPlainText("本地稿。")
        external = self.original + "\n外部修改。".encode("utf-8")
        self.path.write_bytes(external)
        self.assertFalse(w.document_controller.auto_save())
        self.assert_state("conflict", "外部版本变化")
        self.assertEqual(w.editor.retry_save_button.text(), "处理版本冲突")
        with patch("ui.main_window.QMessageBox.exec", lambda box: box.reject()):
            w.editor.retry_save_button.click()
        self.assertEqual(self.path.read_bytes(), external)
        self.assertTrue(w.editor.is_dirty())
        def choose_overwrite(box):
            next(b for b in box.buttons() if b.text() == "覆盖外部修改").click()
        with patch("ui.main_window.QMessageBox.exec", choose_overwrite):
            w.editor.retry_save_button.click()
        self.assert_state("clean", "已保存")
        self.assertIn("本地稿。", self.path.read_text(encoding="utf-8"))

    def test_draft_copy_preserves_error_revision_and_original_file(self):
        w = self.window
        self.body().insertPlainText("本地草稿。")
        w.editor.show_save_error("磁盘只读")
        revision = w.editor.loaded_revision()
        destination = Path(self.tmp.name) / "copy.md"
        with patch("ui.main_window.QFileDialog.getSaveFileName", return_value=(str(destination), "Markdown")):
            w.editor.draft_copy_button.click()
        self.assertEqual(destination.read_text(encoding="utf-8"), w.editor.document_text())
        self.assertEqual(w.editor.loaded_revision(), revision)
        self.assertTrue(w.editor.is_dirty())
        self.assert_state("error", "保存失败")
        self.assertEqual(self.path.read_bytes(), self.original)
        with patch("ui.main_window.QFileDialog.getSaveFileName", return_value=(str(self.path), "Markdown")), \
             patch("ui.main_window.QMessageBox.warning") as warning:
            w.editor.draft_copy_button.click()
        warning.assert_called_once()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_reload_keeps_draft_until_explicit_discard(self):
        w = self.window
        self.body().insertPlainText("本地稿。")
        w.editor.show_save_error("保存失败")
        source = w.editor.document_text()
        with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            w.editor.reload_button.click()
        self.assertEqual(w.editor.document_text(), source)
        self.assert_state("error", "保存失败")
        with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            w.editor.reload_button.click()
        self.assert_state("clean", "已保存")
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(w.editor.is_dirty())

    def test_late_notifications_never_mark_another_document_saved(self):
        w = self.window
        w._on_file_selected("章节", str(self.second))
        self.body().insertPlainText("第二章草稿。")
        revision = w.editor.loaded_revision()
        w.document_controller.auto_saved.emit(str(self.path))
        w.document_controller.save_conflict_detected.emit(str(self.path))
        self.assert_state("dirty", "未保存")
        self.assertIn(str(self.second), w.save_status.toolTip())
        self.assertEqual(w.editor.loaded_revision(), revision)
        self.assertTrue(w.editor.is_dirty())

    def test_hidden_editor_save_does_not_replace_visible_canon_state(self):
        w = self.window
        page = self.canon()
        self.name_field(page).setText("资料草稿")
        self.assert_state("dirty", "未保存")
        self.body().insertPlainText("后台正文修改。")
        self.assertTrue(w.document_controller.auto_save())
        self.assert_state("dirty", "未保存")
        self.assertIn(str(self.card), w.save_status.toolTip())
        self.assertTrue(page.is_modified())

    def test_canon_real_save_and_auto_save_publish_progress_without_header_duplicate(self):
        w = self.window
        page = self.canon()
        self.name_field(page).setText("已自动保存")
        self.assert_state("dirty", "未保存")
        self.assertTrue(page.status_row.isHidden())
        before = self.card.read_bytes()
        def write(path, content, **kwargs):
            self.assert_state("saving", "正在保存…")
            self.assertEqual(self.card.read_bytes(), before)
            return atomic_write_text(path, content, **kwargs)
        with patch("ui.canon_form_widget.atomic_write_text", side_effect=write):
            page._auto_save_flush()
        self.assert_state("clean", "已保存")
        self.assertFalse(page.is_modified())
        self.assertIn("已自动保存", self.card.read_text(encoding="utf-8"))
        self.assertTrue(page.status_row.isHidden())

    def test_canon_failed_auto_save_and_conflict_keep_banner_and_recovery(self):
        w = self.window
        page = self.canon()
        self.name_field(page).setText("资料草稿")
        before = self.card.read_bytes()
        with patch("ui.canon_form_widget.atomic_write_text", side_effect=OSError("写入失败")):
            page._auto_save_flush()
        self.assert_state("error", "保存失败")
        self.assertIn("写入失败", page.save_error_banner.text())
        self.assertTrue(page.save_error_banner.isVisible())
        self.assertTrue(page.form.feedback.isHidden())
        self.assertTrue(page.retry_save_button.isVisible())
        self.assertEqual(self.card.read_bytes(), before)
        page.retry_save_button.click()
        self.assert_state("clean", "已保存")
        self.name_field(page).setText("第二次草稿")
        external = self.card.read_bytes() + "\n外部写入。".encode("utf-8")
        self.card.write_bytes(external)
        page._auto_save_flush()
        self.assert_state("conflict", "外部版本变化")
        w.ai_task_view_controller.set_status("AI 任务完成")
        self.assertTrue(page.diff_button.isVisible())
        self.assertTrue(page.draft_button.isVisible())
        self.assertTrue(page.reload_button.isVisible())
        self.assertFalse(page.retry_save_button.isVisible())
        self.assertEqual(self.card.read_bytes(), external)

    def test_old_canon_success_timer_does_not_clear_current_conflict(self):
        page = self.canon()
        self.name_field(page).setText("保存一次")
        self.assertTrue(page.write_changes()[0])
        another = self.card.with_name("另一张卡.md")
        another.write_text(character_text(), encoding="utf-8")
        page.open_path(another, "character", self.project)
        self.name_field(page).setText("新资料草稿")
        external = another.read_bytes() + b"\nexternal"
        another.write_bytes(external)
        self.assertFalse(page.write_changes()[0])
        page._saved_feedback_timer.timeout.emit()
        self.assert_state("conflict", "外部版本变化")
        self.assertIn(str(another), self.window.save_status.toolTip())
        self.assertEqual(another.read_bytes(), external)

    def test_settings_and_non_document_routes_have_independent_feedback(self):
        w = self.window
        w._show_route("settings")
        self.assertTrue(w.save_status.isHidden())
        w.settings_page.editor_font_size.setValue(23)
        self.assertTrue(w.settings_page.has_unsaved_changes())
        w.document_controller.auto_saved.emit(str(self.path))
        self.assertTrue(w.settings_page.has_unsaved_changes())
        self.assertTrue(w.save_status.isHidden())
        w.settings_page.discard_button.click()
        w._show_route("writing")
        self.assert_state("clean", "已保存")
        w.editor.clear_document()
        self.assertTrue(w.save_status.isHidden())

    def test_story_plan_draft_copy_keeps_json_format_and_save_error(self):
        w = self.window
        path = self.project.outline_dir / "story_plan.json"
        w._on_file_selected("故事规划", str(path))
        self.assertTrue(w.editor.is_story_plan())
        before = path.read_bytes()
        field = next(iter(w.editor.story_plan_form.fields.values()))
        field.insertPlainText("全书方向草稿")
        w.editor.show_save_error("保存失败")
        destination = Path(self.tmp.name) / "规划草稿.json"
        with patch("ui.main_window.QFileDialog.getSaveFileName", return_value=(str(destination), "JSON")) as picker:
            w.editor.draft_copy_button.click()
        self.assertTrue(picker.call_args.args[2].endswith("-草稿.json"))
        self.assertEqual(picker.call_args.args[3], "JSON (*.json)")
        self.assertEqual(destination.read_text(encoding="utf-8"), w.editor.document_text())
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(w.editor.is_dirty())
        self.assert_state("error", "保存失败")

    def test_narrow_theme_large_font_keeps_error_actions_and_status_in_bounds(self):
        w = self.window
        self.body().insertPlainText("草稿。")
        w.editor.show_save_error("磁盘无法写入，本地草稿仍保留。")
        for theme, colors in (("light", LIGHT_COLORS), ("dark", DARK_COLORS)):
            w.appearance_controller.apply({**w.config, **colors, "theme": theme, "ui_font_size": 22})
            w.resize(1100, 800)
            w._refresh_save_feedback()
            QTest.qWait(40)
            for button in (w.editor.retry_save_button, w.editor.draft_copy_button, w.editor.reload_button):
                self.assertTrue(button.isVisible())
                self.assertTrue(w.editor.save_recovery_bar.rect().contains(button.geometry()))
            self.assertTrue(w.statusBar().rect().contains(w.save_status.geometry()))
            self.assert_state("error", "保存失败")
        self.assertEqual(self.path.read_bytes(), self.original)
