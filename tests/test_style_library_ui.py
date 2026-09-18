import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtGui import QFontDatabase, QFont
from pathlib import Path
from ui.style_library_dialog import StyleLibraryDialog, SampleDialog
from core.style_library import empty_library

class StyleLibraryUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        # The Windows offscreen platform does not discover all CJK fonts.
        font_path = Path("C:/Windows/Fonts/NotoSansSC-VF.ttf")
        if font_path.is_file():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families: cls.app.setFont(QFont(families[0]))
    def test_candidate_requires_save_and_does_not_mutate_input(self):
        value=empty_library();value["samples"]=[{"id":"a","title":"测试","source":"本人","text":"门开了。","traits":"简练","scene":"通用","enabled":True}]
        dialog=StyleLibraryDialog(value,"chapter_01")
        self.assertEqual(len(dialog.fields),7)
        self.assertTrue(all(edit.placeholderText() for edit in dialog.fields.values()))
        self.assertTrue(all(not edit.toPlainText() for edit in dialog.fields.values()))
        dialog.fields["描写"].setPlainText("简练")
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
    def test_unified_requirements_status_and_cancel(self):
        value = empty_library()
        value["samples"] = [{"id":"a", "title":"对话样文", "source":"本人", "text":"门开了。", "traits":"简练", "scene":"对话", "enabled":True}]
        dialog = StyleLibraryDialog(value, "chapter_01", quotes=("原句",))
        self.assertEqual(dialog.tabs.tabText(0), "文风要求")
        self.assertEqual(dialog.tabs.count(), 3)
        dialog.fields["总体气质"].setPlainText("克制")
        self.assertIn("文风要求：已填写", dialog.usage.text())
        self.assertIn("已停用", dialog.samples.item(0).text())
        dialog.enabled.setChecked(True)
        self.assertIn("场景不匹配", dialog.samples.item(0).text())
        dialog.scene.setCurrentText("对话")
        self.assertIn("预计纳入", dialog.samples.item(0).text())
        for edit in dialog.fields.values(): edit.setPlainText("要求" * 300)
        self.assertIn("预算或数量上限省略", dialog.samples.item(0).text())
        dialog.exceptions.setCurrentRow(0); dialog._remove_exception()
        self.assertEqual(dialog.quotes(), [])
        dialog.reject()
        self.assertFalse(value["enabled"])
        self.assertEqual(value["chapter_scenes"], {})
        dialog.deleteLater()

    def test_narrow_themes_large_fonts(self):
        from ui.theme import apply_theme
        for theme in ("light","dark"):
            apply_theme(self.app,{"theme":theme,"ui_font_size":18})
            for dialog in (StyleLibraryDialog(empty_library(),"chapter_01"),SampleDialog()):
                dialog.resize(480,480);dialog.show();self.app.processEvents()
                self.assertLessEqual(dialog.minimumSizeHint().width(),480)
                self.assertTrue(dialog.isVisible())
                if isinstance(dialog, StyleLibraryDialog):
                    for index in range(dialog.tabs.count()):
                        dialog.tabs.setCurrentIndex(index)
                        self.app.processEvents()
                        self.assertLessEqual(dialog.minimumSizeHint().width(), 480)
                        self.assertLessEqual(dialog.minimumSizeHint().height(), 480)
                        if index == 1:
                            self.assertGreaterEqual(dialog.usage.height(), dialog.usage.heightForWidth(dialog.usage.width()))
                dialog.close();dialog.deleteLater()
