"""Typography settings must remain display-only and preserve existing profiles."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFontDatabase, QInputMethodEvent, QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from core.config import CONFIG_SCHEMA_VERSION, DEFAULT_CONFIG, load_config, normalize_config
from core.expansion import ExpansionRunResult
from core.project import NovelProject
from ui.main_window import MainWindow


class TypographyConfigTests(TestCase):
    def test_bundled_example_is_a_current_new_profile(self):
        example = json.loads((Path(__file__).resolve().parents[1] / "config.example.json").read_text(encoding="utf-8"))
        normalized = normalize_config(example)
        self.assertEqual(example["config_schema_version"], CONFIG_SCHEMA_VERSION)
        self.assertEqual((normalized["editor_font_size"], normalized["editor_line_spacing"]), (18, 1.8))
        self.assertEqual(normalized["theme"], "light")

    def test_new_profile_uses_recommended_defaults_without_writing_a_file(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "profile.json"
            with patch("core.config.get_config_path", return_value=target):
                config = load_config()
            self.assertEqual((config["editor_font_size"], config["editor_line_spacing"]), (18, 1.8))
            self.assertFalse(target.exists())

    def test_existing_profiles_keep_font_and_natural_spacing_on_load_and_reload(self):
        with TemporaryDirectory() as temporary:
            target = Path(temporary) / "profile.json"
            for raw, expected in (({"editor_font_size": 16}, 16), ({"editor_font_size": 24}, 24), ({}, 16)):
                target.write_text(json.dumps({"config_schema_version": 6, "last_project": "keep-me", **raw}), encoding="utf-8")
                with patch("core.config.get_config_path", return_value=target):
                    config = load_config()
                    again = load_config()
                self.assertEqual(config, again)
                self.assertEqual((config["editor_font_size"], config["editor_line_spacing"]), (expected, 1.0))
                self.assertEqual(config["last_project"], "keep-me")
                self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["config_schema_version"], CONFIG_SCHEMA_VERSION)

    def test_versioned_missing_fields_and_explicit_values_are_distinguished(self):
        old = normalize_config({"config_schema_version": 6})
        new = normalize_config({"config_schema_version": CONFIG_SCHEMA_VERSION})
        explicit = normalize_config({"config_schema_version": 6, "editor_font_size": 18, "editor_line_spacing": 1.6})
        self.assertEqual((old["editor_font_size"], old["editor_line_spacing"]), (16, 1.0))
        self.assertEqual((new["editor_font_size"], new["editor_line_spacing"]), (18, 1.8))
        self.assertEqual((explicit["editor_font_size"], explicit["editor_line_spacing"]), (18, 1.6))
        self.assertEqual(normalize_config(explicit), explicit)

    def test_invalid_typography_is_finite_bounded_and_legacy_fallback_is_preserved(self):
        for value, expected in ((False, 1.8), (float("nan"), 1.8), (float("inf"), 1.8), ("bad", 1.8), (-1, 1.0), (5, 2.4), ("1.7", 1.7)):
            config = normalize_config({**DEFAULT_CONFIG, "editor_line_spacing": value})
            self.assertEqual(config["editor_line_spacing"], expected)
        for value in (False, float("nan"), float("inf"), "bad"):
            self.assertEqual(normalize_config({**DEFAULT_CONFIG, "editor_font_size": value})["editor_font_size"], 18)
            self.assertEqual(normalize_config({"config_schema_version": 6, "editor_font_size": value})["editor_font_size"], 16)


class TypographySettingsTests(TestCase):
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
        self.config_path = Path(self.tmp.name) / "config.json"
        self.config_path.write_text(json.dumps({"config_schema_version": 6, "editor_font_size": 16, "auto_save": False}), encoding="utf-8")
        config_path = patch("core.config.get_config_path", return_value=self.config_path)
        config_path.start()
        self.addCleanup(config_path.stop)
        restore = patch.object(MainWindow, "_restore_last_project")
        restore.start()
        self.addCleanup(restore.stop)
        self.project = NovelProject.create(Path(self.tmp.name) / "book", "排版设置")
        self.project.save_chapter("chapter_01", "找到灯塔", ("旧站的灯还亮着。" * 40 + "\n\n") * 50, title="旧站")
        self.path = self.project.chapters_dir / "chapter_01.md"
        self.original = self.path.read_bytes()
        self.window = MainWindow(config=load_config(), ui_state_path=Path(self.tmp.name) / "ui.json")
        self.addCleanup(self.close_window)
        self.window.resize(1440, 900)
        self.window.show()
        self.window.project_session.set_project(self.project)
        self.window._show_route("writing")
        self.window._on_file_selected("章节", str(self.path))
        self.page = self.window.settings_page
        self.edit = self.window.editor.writing_page.body_edit
        self.settle()

    def settle(self):
        self.app.processEvents()

    def close_window(self):
        w = self.window
        w.ai_workflow_controller.discard_pending_result()
        w.review_panel.finish()
        w.editor._set_dirty(False)
        w.structured_canon_page.reset()
        w.settings_page.set_config(w.config)
        w.close()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def set_draft(self, size=24, spacing=2.0):
        self.page.editor_font_size.setValue(size)
        self.page.editor_line_spacing.setValue(spacing)
        self.settle()

    def test_preview_changes_and_discard_never_change_live_prose(self):
        self.set_draft()
        self.assertTrue(self.page.has_unsaved_changes())
        self.assertEqual(self.page.typography_preview.font().pixelSize(), 24)
        self.assertEqual(self.page.typography_preview.line_spacing(), 2.0)
        self.assertEqual((self.edit.font().pixelSize(), self.edit.line_spacing()), (16, 1.0))
        self.page.discard_button.click()
        self.assertFalse(self.page.has_unsaved_changes())
        self.assertEqual((self.page.typography_preview.font().pixelSize(), self.page.typography_preview.line_spacing()), (16, 1.0))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_restore_recommended_is_a_draft_and_save_persists_only_settings(self):
        self.page.chapter_target_chars.setValue(4200)
        self.page.typography_reset.click()
        self.assertEqual((self.page.editor_font_size.value(), self.page.editor_line_spacing.value()), (18, 1.8))
        self.assertEqual(self.page.chapter_target_chars.value(), 4200)
        self.assertEqual(self.edit.line_spacing(), 1.0)
        self.page._emit_save()
        self.settle()
        self.assertFalse(self.page.has_unsaved_changes())
        self.assertEqual((self.edit.font().pixelSize(), self.edit.line_spacing()), (18, 1.8))
        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual((saved["editor_font_size"], saved["editor_line_spacing"]), (18, 1.8))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_saved_typography_preserves_live_draft_selection_undo_and_revision(self):
        self.edit.moveCursor(QTextCursor.MoveOperation.End)
        self.edit.insertPlainText("作者草稿。")
        cursor = self.edit.textCursor()
        cursor.setPosition(35)
        cursor.setPosition(8, QTextCursor.MoveMode.KeepAnchor)
        self.edit.setTextCursor(cursor)
        source = self.window.editor.document_text()
        revision = self.window.editor.loaded_revision()
        steps = self.edit.document().availableUndoSteps()
        changed = QSignalSpy(self.edit.document().contentsChanged)
        saved = QSignalSpy(self.window.editor.file_saved)
        self.set_draft()
        self.page._emit_save()
        self.settle()
        self.assertEqual(self.window.editor.document_text(), source)
        self.assertEqual(self.window.editor.loaded_revision(), revision)
        self.assertEqual((self.edit.textCursor().position(), self.edit.textCursor().anchor()), (8, 35))
        self.assertEqual(self.edit.document().availableUndoSteps(), steps)
        self.assertTrue(self.window.editor.is_dirty())
        self.assertEqual((changed.count(), saved.count()), (0, 0))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.edit.undo()
        self.assertNotIn("作者草稿。", self.edit.toPlainText())

    def test_save_failure_keeps_preview_draft_and_live_configuration(self):
        original_config = self.config_path.read_bytes()
        self.set_draft()
        with patch("ui.settings_controller.save_config", side_effect=OSError("磁盘只读")):
            self.page._emit_save()
        self.assertIn("磁盘只读", self.page.feedback.text())
        self.assertTrue(self.page.has_unsaved_changes())
        self.assertEqual(self.page.typography_preview.line_spacing(), 2.0)
        self.assertEqual((self.edit.font().pixelSize(), self.edit.line_spacing()), (16, 1.0))
        self.assertEqual(self.config_path.read_bytes(), original_config)

    def test_line_spacing_alone_is_a_settings_draft_and_survives_theme_toggle(self):
        self.page.editor_line_spacing.setValue(1.7)
        self.assertTrue(self.page.has_unsaved_changes())
        self.window.toggle_theme()
        self.assertEqual(self.page.editor_line_spacing.value(), 1.7)
        self.assertEqual(self.page.typography_preview.line_spacing(), 1.7)
        self.assertEqual(self.edit.line_spacing(), 1.0)
        self.page._emit_save()
        self.assertEqual(self.edit.line_spacing(), 1.7)
        self.assertEqual(self.window.config["theme"], "dark")

    def test_new_preview_draft_clears_old_saved_notice_without_applying_it(self):
        self.page._emit_save()
        self.assertEqual(self.window.status_message.text(), "设置已保存")
        self.page.editor_line_spacing.setValue(1.7)
        self.assertNotEqual(self.window.status_message.text(), "设置已保存")
        self.assertTrue(self.page.has_unsaved_changes())
        self.assertEqual(self.edit.line_spacing(), 1.0)

    def test_real_pending_ai_result_stays_current_after_saving_display_preferences(self):
        w = self.window
        candidate = "灯" * 300
        result = ExpansionRunResult(raw_output=f"<NOVEL_TEXT>{candidate}</NOVEL_TEXT>", first_raw_output=None,
            plain_text_fallback_count=0, target_chars=300, min_chars=240, max_chars=360,
            initial_char_count=300, final_char_count=300)
        token = w.ai_controller.start("expand", self.project, "chapter_01", w.editor.document_text(), lambda _cancel: result)
        self.assertIsNotNone(token)
        for _ in range(200):
            if w.ai_workflow_controller.has_pending_result and not w.ai_controller.is_running():
                break
            QTest.qWait(10)
        self.assertTrue(w.ai_workflow_controller.has_pending_result)
        self.set_draft(22, 1.6)
        self.page._emit_save()
        self.assertTrue(w.ai_workflow_controller._task_context_matches(token))
        self.assertTrue(w.ai_workflow_controller.has_pending_result)
        self.assertNotIn(candidate, w.editor.document_text())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_ime_preedit_survives_font_and_spacing_change_then_commits_normally(self):
        self.edit.setFocus()
        QApplication.sendEvent(self.edit, QInputMethodEvent("zhongwen", []))
        self.window.appearance_controller.apply({**self.window.config, "editor_font_size": 24, "editor_line_spacing": 1.8})
        self.settle()
        self.assertEqual(self.edit.textCursor().block().layout().preeditAreaText(), "zhongwen")
        self.assertEqual(self.edit.spacing_layout.factor, 1.0)
        event = QInputMethodEvent()
        event.setCommitString("中文")
        QApplication.sendEvent(self.edit, event)
        self.assertEqual(self.edit.spacing_layout.factor, 1.8)
        self.assertTrue(self.edit.toPlainText().startswith("中文"))
        self.edit.undo()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_loading_document_clears_preedit_without_leaving_pending_spacing_stuck(self):
        QApplication.sendEvent(self.edit, QInputMethodEvent("weishuru", []))
        self.edit.set_line_spacing(1.8)
        self.assertEqual(self.edit.spacing_layout.factor, 1.0)
        self.assertTrue(self.window.document_controller.reload_current_file())
        self.settle()
        self.assertEqual(self.edit.spacing_layout.factor, 1.8)
        self.assertFalse(self.window.editor.is_dirty())
        self.assertEqual(self.path.read_bytes(), self.original)
