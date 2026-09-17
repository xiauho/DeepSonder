import os
from unittest import TestCase, SkipTest
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton
from core.config import DEFAULT_CONFIG
from ui.main_window import MainWindow

class CompactWorkspaceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        instance = QCoreApplication.instance()
        if instance is not None and not isinstance(instance, QApplication):
            raise SkipTest('Requires QApplication')
        cls.app = instance or QApplication([])

    def setUp(self):
        self.restore = patch.object(MainWindow, '_restore_last_project')
        self.restore.start()
        self.addCleanup(self.restore.stop)
        self.window = MainWindow(config={**DEFAULT_CONFIG, 'auto_save': False})
        self.addCleanup(self.window.close)
        self.window.window_state_controller.activate_route('writing')
        self.window.resize(1100, 720)
        self.window.show()
        self.app.processEvents()

    def test_small_window_reserves_space_for_manuscript(self):
        w = self.window
        self.assertEqual(w.width(), 1100)
        self.assertLessEqual(w.primary_nav.width(), 80)
        self.assertGreaterEqual(w.editor.width(), 650)
        self.assertFalse(w.app_header.isVisible())
        self.assertTrue(w.action_bar.isVisible())
        for button in w.primary_nav.buttons.values():
            self.assertTrue(button.rect().contains(button._text_label.geometry()))
            self.assertTrue(button.accessibleName())

    def test_panel_buttons_restore_panels_and_track_shortcuts(self):
        w = self.window
        self.assertFalse(w.panel_buttons['inspector'].isChecked())
        w.panel_buttons['inspector'].click()
        self.app.processEvents()
        self.assertTrue(w.inspector.isVisible())
        w.actions['inspector'].trigger()
        self.assertFalse(w.inspector.isVisible())
        self.assertFalse(w.panel_buttons['inspector'].isChecked())
        w.panel_buttons['navigation'].click()
        self.assertFalse(w.left_panel.isVisible())
        w.window_state_controller.activate_route('reports')
        w.window_state_controller.activate_route('writing')
        self.assertFalse(w.left_panel.isVisible())
        w.panel_buttons['navigation'].click()
        self.assertTrue(w.left_panel.isVisible())

    def test_inspector_close_button_hides_panel_and_keeps_controls_in_sync(self):
        w = self.window
        close_button = w.inspector.findChild(QToolButton, 'panelToggleButton')
        self.assertIsNotNone(close_button)
        for theme in ('light', 'dark'):
            with self.subTest(theme=theme):
                if w.window_state_controller.inspector_visible:
                    w.toggle_inspector()
                w.appearance_controller.apply({**w.config, 'theme': theme})
                w.panel_buttons['inspector'].click()
                self.app.processEvents()
                self.assertTrue(w.inspector.isVisible())
                editor_width = w.editor.width()
                QTest.mouseClick(close_button, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                self.assertFalse(w.inspector.isVisible())
                self.assertFalse(w.window_state_controller.inspector_visible)
                self.assertFalse(w.panel_buttons['inspector'].isChecked())
                self.assertGreater(w.editor.width(), editor_width)

                w.window_state_controller.activate_route('reports')
                w.window_state_controller.activate_route('writing')
                self.app.processEvents()
                self.assertFalse(w.inspector.isVisible())

                w.actions['inspector'].trigger()
                self.app.processEvents()
                self.assertTrue(w.inspector.isVisible())
                self.assertTrue(w.window_state_controller.inspector_visible)
                self.assertTrue(w.panel_buttons['inspector'].isChecked())
                QTest.mouseClick(close_button, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                self.assertFalse(w.inspector.isVisible())

    def test_task_menu_keeps_records_reachable_when_actions_disabled(self):
        w = self.window
        for key in ('expand', 'continuation', 'check', 'memory'):
            w.actions[key].setEnabled(False)
            self.assertIn(w.actions[key], w.ai_creation_menu.actions())
        self.assertTrue(w.ai_creation_button.isEnabled())
        self.assertIn(w.actions['output'], w.ai_creation_menu.actions())
        for key in ('new_project', 'open_project', 'export', 'trash'):
            self.assertIn(w.actions[key], w.left_panel.project_menu.actions())

    def test_save_failure_remains_visible_in_compact_header(self):
        self.window.editor.show_save_error('磁盘只读')
        self.assertTrue(self.window.editor.path_label.isVisible())
        self.assertIn('磁盘只读', self.window.editor.path_label.text())

    def test_narrow_editor_stacks_tools_without_losing_title(self):
        w = self.window
        w.toggle_inspector()
        self.app.processEvents()
        self.assertTrue(w.editor._actions_stacked)
        self.assertGreater(w.editor.title_label.width(), 100)
        self.assertTrue(w.action_bar.isVisible())
        w.resize(1600, 900)
        self.app.processEvents()
        self.assertFalse(w.editor._actions_stacked)
        self.assertTrue(w.action_bar.isVisible())
