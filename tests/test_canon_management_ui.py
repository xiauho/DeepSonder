import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QToolButton, QMessageBox, QLabel
from PySide6.QtGui import QFontDatabase
from core.canon_fields import add_field, add_group
from ui.theme import apply_theme
from core.canon_schema import parse_canon_entry, render_canon_entry, add_module
from tests.test_canon_index import FakeProject
from tests.test_canon_schema import character_text
from ui.canon_form_widget import CanonFormWidget


class CanonManagementUITests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = FakeProject(Path(self.tmp.name))
        self.path = self.project.canon_dir / "characters" / "白鸥.md"
        self.path.write_text(character_text(), encoding="utf-8")
        self.form = CanonFormWidget()
        self.form.load_entry(self.path, "character", self.project)
        self.form.resize(520, 680)
        self.form.show()
        self.app.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.form.reset()
        self.form.close()
        self.form.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def create(self, name, role):
        self.form._change_structure(lambda draft: add_module(draft, name, role))
        return self.form.entry.section(name)

    def field(self, name):
        return next(e for s, k, e in self.form._field_widgets if k == name)

    def menu(self, section, group=None, field=None):
        return next(b.menu() for b in self.form.findChildren(QToolButton)
            if b.objectName() == "canonManageButton" and b.property("moduleId") == section.module_id
            and b.property("groupName") == group and b.property("fieldName") == field)

    def action(self, menu, text):
        return next(a for a in menu.actions() if a.text() == text)

    def test_custom_fields_can_be_added_renamed_sorted_deleted_and_reopened(self):
        section = self.create("习惯", "fields")
        for name in ("偏好", "忌讳"):
            button = next(b for b in self.form.findChildren(QPushButton)
                          if b.text() == "＋ 添加字段" and b.property("moduleId") == section.module_id)
            with patch("ui.canon_form_widget.QInputDialog.getText", return_value=(name, True)):
                button.click()
            self.field(name).setText(name + "内容")
        section = self.form.entry.section("习惯")
        down = self.action(self.menu(section, field="偏好"), "下移字段")
        self.assertTrue(down.isEnabled())
        down.trigger()
        section = self.form.entry.section("习惯")
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("喜好", True)):
            self.action(self.menu(section, field="偏好"), "重命名字段…").trigger()
        self.assertEqual(self.field("喜好").text(), "偏好内容")
        with patch("ui.canon_form_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.action(self.menu(self.form.entry.section("习惯"), field="忌讳"), "删除字段…").trigger()
        self.assertTrue(self.form.write_changes()[0])
        self.form.load_entry(self.path, "character", self.project)
        self.assertEqual(self.form.entry.section("习惯").fields, {"喜好": "偏好内容"})

    def test_group_menu_reorders_renames_and_deletes_with_unsaved_input(self):
        section = self.form.entry.section("能力档案")
        editor = next(e for s, g, k, e in self.form._group_widgets if g == "潮汐感知" and k == "当前等级")
        editor.setText("二阶")
        self.action(self.menu(section, "潮汐感知"), "下移条目").trigger()
        section = self.form.entry.section("能力档案")
        self.assertEqual([g.name for g in section.groups], ["忆潮", "潮汐感知"])
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("感潮", True)):
            self.action(self.menu(section, "潮汐感知"), "重命名条目…").trigger()
        with patch("ui.canon_form_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.action(self.menu(self.form.entry.section("能力档案"), "忆潮"), "删除条目…").trigger()
        self.assertTrue(self.form.write_changes()[0])
        loaded = parse_canon_entry("character", self.path.read_text(encoding="utf-8"))
        self.assertEqual([g.name for g in loaded.section("能力档案").groups], ["感潮"])
        self.assertEqual(loaded.section("能力档案").groups[0].fields["当前等级"], "二阶")

    def test_group_rename_restores_focus_and_selection_in_same_field(self):
        editor = next(e for s, g, k, e in self.form._group_widgets if g == "潮汐感知" and k == "当前等级")
        self.form.form_scroll.ensureWidgetVisible(editor)
        editor.setFocus(); editor.setSelection(0, 1); self.app.processEvents()
        self.assertIs(self.form._last_editor, editor)
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("感潮", True)):
            self.form._rename_group(self.form.entry.section("能力档案"), "潮汐感知")
        self.app.processEvents()
        current = next(e for s, g, k, e in self.form._group_widgets if g == "感潮" and k == "当前等级")
        self.assertIs(QApplication.focusWidget(), current)
        self.assertEqual(current.selectedText(), "一")

    def test_invalid_add_and_cancelled_delete_keep_inputs_and_disk(self):
        section = self.create("习惯", "fields")
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("偏好", True)):
            self.form._add_field(section)
        self.field("偏好").setText("尚未保存")
        before = self.path.read_bytes()
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("偏好", True)):
            self.form._add_field(self.form.entry.section("习惯"))
        self.assertIn("已存在", self.form.feedback.text())
        self.assertEqual(self.field("偏好").text(), "尚未保存")
        with patch("ui.canon_form_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.form._delete_field(self.form.entry.section("习惯"), "偏好")
        self.assertEqual(self.field("偏好").text(), "尚未保存")
        self.assertEqual(self.path.read_bytes(), before)

    def test_collapse_keyboard_saves_hidden_input_without_dirtying_view_state(self):
        section = self.form.entry.section("基础档案")
        toggle = self.form._module_toggles[section.module_id]
        toggle.setFocus()
        QTest.keyClick(toggle, Qt.Key.Key_Space)
        self.assertFalse(toggle.isChecked())
        self.assertFalse(self.form.is_modified())
        self.field("姓名").setText("白羽")
        self.assertTrue(self.form.write_changes()[0])
        self.assertFalse(self.form._module_bodies[section.module_id].isVisible())
        self.form.load_entry(self.path, "character", self.project)
        self.assertFalse(self.form._module_toggles[section.module_id].isChecked())
        self.assertEqual(self.field("姓名").text(), "白羽")
        self.assertFalse(self.form.is_modified())

    def test_collapse_state_is_per_document_and_survives_structure_changes(self):
        section = self.form.entry.section("外貌与辨识特征")
        identity = section.module_id
        self.form._module_toggles[identity].setChecked(False)
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("容貌", True)):
            self.form._rename_module(section)
        self.assertFalse(self.form._module_toggles[identity].isChecked())
        self.assertTrue(self.form.write_changes()[0])
        other = self.path.with_name("另一份.md")
        other.write_bytes(self.path.read_bytes())
        self.form.load_entry(other, "character", self.project)
        self.assertTrue(self.form._module_toggles[identity].isChecked())
        self.form.load_entry(self.path, "character", self.project)
        self.assertFalse(self.form._module_toggles[identity].isChecked())
        self.form.reset()
        self.form.load_entry(self.path, "character", self.project)
        self.assertTrue(self.form._module_toggles[identity].isChecked())

    def test_undo_reveals_collapsed_last_editor(self):
        section = self.form.entry.section("基础档案")
        editor = self.field("姓名")
        editor.setFocus(); QTest.keyClicks(editor, "x")
        self.form._module_toggles[section.module_id].setChecked(False)
        self.form.edit_command("undo")
        self.assertTrue(self.form._module_toggles[section.module_id].isChecked())
        self.assertEqual(editor.text(), "白鸥")

    def test_only_custom_fields_expose_management_menu(self):
        section = self.form.entry.section("基础档案")
        self.assertFalse([b for b in self.form.findChildren(QToolButton)
                         if b.property("moduleId") == section.module_id and b.property("fieldName") == "姓名"])
        self.form.set_modules_expanded(False)
        managed = next(s for s in self.form.entry.sections if s.role == "managed")
        self.assertFalse(self.form._module_bodies[managed.module_id].isVisible())
        self.assertFalse(self.form.is_modified())


    def test_narrow_large_font_long_names_both_themes_without_horizontal_overflow(self):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
        section = self.create("长名称字段模块" * 12, "fields")
        self.form._change_structure(lambda draft: add_field(draft, section.module_id, "VeryLongFieldName" * 10))
        section = self.create("长名称分组模块" * 12, "group")
        self.form._change_structure(lambda draft: add_group(draft, section.module_id, "长条目名称" * 24))
        self.form._change_structure(lambda draft: add_field(draft, section.module_id, "长字段" * 30, "长条目名称" * 24))
        for theme in ("light", "dark"):
            apply_theme(self.app, {"theme":theme, "ui_font_size":18})
            self.form.resize(520, 680); self.app.processEvents()
            scroll = self.form.form_scroll
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0, theme)
            self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
            labels = [label for label in self.form.findChildren(QLabel) if label.objectName() == "canonFieldLabel"]
            self.assertTrue(labels)
            for label in labels:
                self.assertGreater(label.width(), 80, label.text())
                self.assertGreaterEqual(label.height(), label.fontMetrics().height())
                if len(label.text()) > 100:
                    self.assertGreater(label.height(), label.fontMetrics().height() * 2)
            self.form.set_modules_expanded(False); self.app.processEvents()
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.form.set_modules_expanded(True)
        apply_theme(self.app, {"theme":"light"})


if __name__ == "__main__":
    import unittest
    unittest.main()
