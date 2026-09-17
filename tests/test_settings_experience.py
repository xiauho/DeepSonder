import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QPoint, QRect
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox
from core.config import DEFAULT_CONFIG
from ui.pages import SettingsPage
from ui.main_window import MainWindow


class SettingsExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = QCoreApplication.instance()
        if app is not None and not isinstance(app, QApplication):
            raise unittest.SkipTest("Requires QApplication")
        cls.app = app or QApplication([])

    def setUp(self):
        self.page = SettingsPage(DEFAULT_CONFIG)
        self.addCleanup(self.page.deleteLater)
        self.addCleanup(self.page.close)
        self.page.resize(900, 630)
        self.page.show()
        self.app.processEvents()

    def test_draft_discard_and_staged_defaults(self):
        page = self.page
        self.assertFalse(page.has_unsaved_changes())
        self.assertFalse(page.save_button.isEnabled())
        page.editor_font_size.setValue(23)
        self.assertTrue(page.has_unsaved_changes())
        page.discard_button.click()
        self.assertFalse(page.has_unsaved_changes())
        page.set_config({**DEFAULT_CONFIG, "editor_font_size": 24, "last_project": "keep-this-metadata"})
        saved = []
        page.save_requested.connect(saved.append)
        page._restore_defaults()
        self.assertTrue(page.has_unsaved_changes())
        self.assertEqual(saved, [])
        self.assertEqual(page.config()["last_project"], "keep-this-metadata")
        page.discard_button.click()
        self.assertEqual(page.editor_font_size.value(), 24)

    def test_validation_reveals_advanced_field_and_blocks_save_and_test(self):
        page = self.page
        saves, tests = [], []
        page.save_requested.connect(saves.append)
        page.test_requested.connect(tests.append)
        page.launcher_args.setText('"unfinished')
        page._emit_save()
        page._emit_test()
        self.assertEqual(saves, [])
        self.assertEqual(tests, [])
        self.assertEqual(page.tabs.currentIndex(), 1)
        self.assertTrue(page.advanced_toggle.isChecked())
        self.assertFalse(page.parameter_errors[page.launcher_args].isHidden())
        self.assertIn("启动参数", page.feedback.text())
        self.app.processEvents()
        self.app.processEvents()
        viewport = page.section_scrolls[1].viewport()
        error = page.parameter_errors[page.launcher_args]
        self.assertTrue(viewport.rect().contains(QRect(error.mapTo(viewport, QPoint(0, 0)), error.size())))
        page.launcher_args.setText('--name "two words"')
        self.assertTrue(page.parameter_errors[page.launcher_args].isHidden())
        page._emit_save()
        self.assertEqual(saves[-1]["dsh_launcher_args"], ["--name", "two words"])

    def test_conditional_rows_and_custom_values_survive_tab_changes(self):
        page = self.page
        self.assertFalse(page.writing_form.isRowVisible(page.ai_context_history_chapters))
        page.ai_history_mode.setCurrentIndex(page.ai_history_mode.findData("custom"))
        self.assertTrue(page.writing_form.isRowVisible(page.ai_context_history_chapters))
        page.ai_context_history_chapters.setValue(12)
        page.tabs.setCurrentIndex(1)
        page.model_context_window.setCurrentIndex(page.model_context_window.findData(-1))
        self.assertTrue(page.ai_form.isRowVisible(page.custom_context_window))
        page.tabs.setCurrentIndex(0)
        self.assertEqual(page.ai_context_history_chapters.value(), 12)

    def test_save_error_survives_editing_and_clears_on_acknowledgement(self):
        page = self.page
        page.editor_font_size.setValue(25)
        page.show_save_error("disk full")
        page.editor_font_size.setValue(26)
        self.assertIn("disk full", page.feedback.text())
        page.set_config(page.config())
        self.assertFalse(page.has_unsaved_changes())
        self.assertEqual(page.feedback.text(), "设置已保存")


class SettingsWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        SettingsExperienceTests.setUpClass()
        cls.app = SettingsExperienceTests.app

    def setUp(self):
        patcher = patch.object(MainWindow, "_restore_last_project")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False})
        self.addCleanup(self.cleanup_window)
        self.page = self.window.settings_page

    def cleanup_window(self):
        self.page.set_config(self.window.config)
        self.window.close()
        self.window.deleteLater()

    def test_draft_survives_route_change_and_external_theme_update(self):
        window = self.window
        window._show_route("settings")
        self.page.editor_font_size.setValue(25)
        window._show_route("dashboard")
        window._show_route("settings")
        self.assertEqual(self.page.editor_font_size.value(), 25)
        with patch("ui.settings_controller.save_config"):
            window.toggle_theme()
            self.assertEqual(self.page.editor_font_size.value(), 25)
            self.page._emit_save()
        self.assertEqual(window.config["editor_font_size"], 25)
        self.assertEqual(window.config["theme"], "dark")
        self.assertFalse(self.page.has_unsaved_changes())

    def test_disk_failure_preserves_runtime_configuration_and_draft(self):
        self.page.editor_font_size.setValue(25)
        previous = self.window.settings_controller.config
        with patch("ui.settings_controller.save_config", side_effect=OSError("read only")):
            self.page._emit_save()
        self.assertEqual(self.window.settings_controller.config, previous)
        self.assertEqual(self.page.editor_font_size.value(), 25)
        self.assertTrue(self.page.has_unsaved_changes())
        self.assertIn("read only", self.page.feedback.text())

    def test_discard_uses_latest_saved_theme_and_preserves_project_metadata(self):
        self.page.editor_font_size.setValue(25)
        with patch("ui.settings_controller.save_config"):
            self.window.toggle_theme()
        self.page.discard_button.click()
        self.assertEqual(self.page.theme.currentData(), "dark")
        self.assertFalse(self.page.has_unsaved_changes())
        self.page.editor_font_size.setValue(25)
        self.window.config["last_project"] = "new-project"
        with patch("ui.settings_controller.save_config"):
            self.page._emit_save()
        self.assertEqual(self.window.config["last_project"], "new-project")
        with patch("ui.settings_controller.save_config"):
            self.window.toggle_theme()  # Saved light baseline.
            self.page.editor_font_size.setValue(26)
            self.window.toggle_theme()  # External dark update while draft exists.
            self.page._restore_defaults()
            self.page._emit_save()
        self.assertEqual(self.window.config["theme"], "light")
        self.assertEqual(self.window.config["last_project"], "new-project")

    def test_exit_save_and_discard_respect_choice(self):
        self.page.editor_font_size.setValue(25)
        with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Save), patch("ui.settings_controller.save_config") as save:
            event = QCloseEvent()
            self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())
        save.assert_called_once()
        self.assertFalse(self.page.has_unsaved_changes())
        self.page.editor_font_size.setValue(26)
        with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Discard), patch("ui.settings_controller.save_config") as save:
            event = QCloseEvent()
            self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())
        save.assert_not_called()

    def test_exit_cancel_and_failed_save_do_not_close(self):
        self.page.editor_font_size.setValue(25)
        event = QCloseEvent()
        with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Save), patch("ui.settings_controller.save_config", side_effect=OSError("disk full")):
            event = QCloseEvent()
            self.window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertTrue(self.page.has_unsaved_changes())
