import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from PySide6.QtWidgets import QApplication, QDialog
from ui.style_library_dialog import StyleLibraryDialog, SampleDialog
from core.style_library import empty_library

class StyleLibraryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
    def test_candidate_requires_save_and_does_not_mutate_input(self):
        value=empty_library();value["samples"]=[{"id":"a","title":"测试","source":"本人","text":"门开了。","traits":"简练","scene":"通用","enabled":True}]
        dialog=StyleLibraryDialog(value,"chapter_01")
        dialog._draft();self.assertEqual(dialog.fields["描写"].toPlainText(),"简练")
        self.assertFalse(dialog.enabled.isChecked());self.assertEqual(value["profile"],{})
        dialog.reject();dialog.deleteLater()
    def test_profile_validation_and_scene(self):
        dialog=StyleLibraryDialog(empty_library(),"chapter_01")
        dialog.fields["对话"].setPlainText("字"*601);dialog._submit()
        self.assertNotEqual(dialog.result(),QDialog.DialogCode.Accepted)
        dialog.fields["对话"].setPlainText("保留停顿");dialog.scene.setCurrentText("对话");dialog._submit()
        self.assertEqual(dialog.value["chapter_scenes"]["chapter_01"],"对话");dialog.deleteLater()
    def test_sample_requires_source_and_features(self):
        dialog=SampleDialog();dialog._submit();self.assertTrue(dialog.error.text())
        for key, edit in dialog.fields.items():
            if hasattr(edit,"setPlainText"):edit.setPlainText("测试")
            else:edit.setText("测试")
        dialog._submit();self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted);dialog.deleteLater()
    def test_narrow_themes_large_fonts(self):
        from ui.theme import apply_theme
        for theme in ("light","dark"):
            apply_theme(self.app,{"theme":theme,"ui_font_size":18})
            for dialog in (StyleLibraryDialog(empty_library(),"chapter_01"),SampleDialog()):
                dialog.resize(480,480);dialog.show();self.app.processEvents()
                self.assertLessEqual(dialog.minimumSizeHint().width(),480)
                self.assertTrue(dialog.isVisible());dialog.close();dialog.deleteLater()
