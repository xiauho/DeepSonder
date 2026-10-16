import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea
from PySide6.QtGui import QFontDatabase
from core.project import NovelProject
from core.character_identity import load_registry
from ui.character_identity_dialog import CharacterIdentityDialog
from ui.theme import apply_theme


class CharacterIdentityUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc") if Path("C:/Windows/Fonts/msyh.ttc").exists() else -1

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = NovelProject.create(Path(self.tmp.name)/'novel', '身份')
        self.project.save_story_state({'characters': {'白鸥': {'state': '健康'}, '林舟': {'state': '受伤'}}})
        self.dialog = CharacterIdentityDialog(self.project)
        self.addCleanup(self.dialog.close)
        self.addCleanup(lambda: apply_theme(self.app, {'theme':'light'}))
        self.dialog.primary.setText('林舟')
        self.dialog.names.setPlainText('白鸥\n林舟')
        self.dialog.evidence.setPlainText('作者确认同一人物')

    def test_preview_choice_save_and_invalidated_preview(self):
        self.assertFalse(self.dialog.save_button.isEnabled())
        self.dialog.preview()
        self.dialog.save()
        self.assertEqual(load_registry(self.project)['groups'], [])
        self.assertIn('冲突字段', self.dialog.feedback.text())
        self.dialog.choices['state'].setCurrentIndex(2)
        self.dialog.names.appendPlainText('无名客')
        self.assertFalse(self.dialog.save_button.isEnabled())
        self.dialog.preview()
        self.dialog.choices['state'].setCurrentIndex(2)
        self.dialog.save()
        self.assertEqual(len(load_registry(self.project)['groups']), 1)

    def test_cancel_does_not_write(self):
        self.dialog.preview()
        self.dialog.reject()
        self.assertFalse((self.project.memory_dir/'character_identities.json').exists())

    def test_small_window_both_themes_large_font(self):
        for theme in ('light', 'dark'):
            apply_theme(self.app, {'theme':theme, 'ui_font_size':18})
            self.dialog.preview()
            self.dialog.resize(520, 480)
            self.dialog.show()
            self.app.processEvents()
            scroll = self.dialog.findChild(QScrollArea)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
            self.assertLessEqual(self.dialog.width(), self.app.primaryScreen().availableGeometry().width())
            self.assertLessEqual(self.dialog.save_button.mapTo(self.dialog, self.dialog.save_button.rect().bottomRight()).x(), self.dialog.width())
            output = os.environ.get('IDENTITY_UI_CAPTURE_DIR')
            if output:
                Path(output).mkdir(parents=True, exist_ok=True)
                self.dialog.grab().save(str(Path(output)/f'identity-{theme}.png'))


if __name__ == '__main__':
    unittest.main()
