"""Regression coverage for continuous canon editing; all projects are temporary."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from core.canon_schema import parse_canon_entry
from tests.test_canon_index import FakeProject
from tests.test_canon_schema import character_text
from ui.canon_form_widget import CanonFormWidget, NewModuleDialog
from ui.structured_canon_page import StructuredCanonPage


class CanonDraftSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = FakeProject(Path(self.tmp.name))
        self.path = self.project.canon_dir / "characters" / "白鸥.md"
        self.path.write_text(character_text(), encoding="utf-8")
        self.widget = CanonFormWidget()
        self.widget.load_entry(self.path, "character", self.project)

    def tearDown(self):
        self.widget.reset()
        self.widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def name(self):
        return next(e for s, k, e in self.widget._field_widgets if k == "姓名")

    def test_repeated_save_then_external_conflict_preserves_draft(self):
        self.name().setText("第一次")
        self.assertEqual(self.widget.write_changes(), (True, ""))
        self.name().setText("第二次")
        self.assertEqual(self.widget.write_changes(), (True, ""))
        self.assertIn("第二次", self.path.read_text(encoding="utf-8"))
        external = self.path.read_text(encoding="utf-8") + "\n外部补充\n"
        self.path.write_text(external, encoding="utf-8")
        self.name().setText("未保存草稿")
        self.assertFalse(self.widget.write_changes()[0])
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)
        self.assertEqual(self.name().text(), "未保存草稿")
        self.assertTrue(self.widget.is_modified())

    def test_structure_operations_preserve_all_pending_inputs(self):
        for operation in ("new", "rename", "move", "delete", "group"):
            with self.subTest(operation=operation):
                self.path.write_text(character_text(), encoding="utf-8")
                self.widget.load_entry(self.path, "character", self.project)
                self.name().setText("保留姓名")
                text_editor = next(e for s, e in self.widget._text_widgets
                                   if s.heading == "性格、动机与核心欲望")
                text_editor.setPlainText("保留性格草稿")
                section = self.widget.entry.section("外貌与辨识特征")
                if operation == "new":
                    dialog = NewModuleDialog(self.widget)
                    dialog.name_edit.setText("新模块")
                    dialog.confirm()
                elif operation == "rename":
                    with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("新标题", True)):
                        self.widget._rename_module(section)
                elif operation == "move":
                    self.widget._move_module(section, 1)
                elif operation == "delete":
                    with patch("ui.canon_form_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                        self.widget._delete_module(section)
                else:
                    with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("新能力", True)):
                        self.widget._add_group(self.widget.entry.section("能力档案"))
                self.assertEqual(self.name().text(), "保留姓名")
                self.assertEqual(self.widget.write_changes(), (True, ""))
                saved = self.path.read_text(encoding="utf-8")
                self.assertIn("保留姓名", saved)
                self.assertIn("保留性格草稿", saved)

    def test_added_group_marks_dirty_and_failed_save_keeps_inputs(self):
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("新能力", True)):
            self.widget._add_group(self.widget.entry.section("能力档案"))
        self.assertTrue(self.widget.is_modified())
        self.name().setText("保留姓名")
        before = self.path.read_bytes()
        with patch("ui.canon_form_widget.atomic_write_text", side_effect=OSError("disk full")):
            self.assertFalse(self.widget.write_changes()[0])
        self.assertTrue(self.widget.is_modified())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.name().text(), "保留姓名")
        self.assertEqual(self.widget.write_changes(), (True, ""))
        self.assertIn("### 新能力", self.path.read_text(encoding="utf-8"))

    def test_remove_last_relation_is_persisted(self):
        for _section, editor in list(self.widget._relation_rows):
            self.widget._remove_relation_row(editor, editor.parentWidget())
        self.assertEqual(self.widget.write_changes(), (True, ""))
        entry = parse_canon_entry("character", self.path.read_text(encoding="utf-8"))
        self.assertEqual(entry.section("关键关系").text, "")

    def test_same_path_refreshes_clean_document_but_preserves_dirty_draft(self):
        page = StructuredCanonPage()
        self.addCleanup(page.close)
        page.open_path(self.path, "character", self.project)
        external = character_text().replace("- 姓名：白鸥", "- 姓名：外部名字")
        self.path.write_text(external, encoding="utf-8")
        page.open_path(self.path, "character", self.project)
        field = next(e for s, k, e in page.form._field_widgets if k == "姓名")
        self.assertEqual(field.text(), "外部名字")
        field.setText("本地草稿")
        newer = external + "\n其他外部变化\n"
        self.path.write_text(newer, encoding="utf-8")
        page.open_path(self.path, "character", self.project)
        self.assertEqual(field.text(), "本地草稿")
        self.assertTrue(page.is_modified())
        self.assertFalse(page.write_changes()[0])
        self.assertEqual(self.path.read_text(encoding="utf-8"), newer)

    def test_failed_structure_validation_preserves_draft_and_view(self):
        self.widget.show()
        self.app.processEvents()
        self.name().setText("保留姓名草稿")
        self.name().setFocus()
        self.name().setSelection(1, 3)
        self.app.processEvents()
        original_sections = list(self.widget.entry.sections)
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("基础档案", True)):
            self.widget._rename_module(self.widget.entry.section("外貌与辨识特征"))
        self.assertEqual(self.name().text(), "保留姓名草稿")
        self.assertTrue(all(a is b for a, b in zip(original_sections, self.widget.entry.sections)))
        self.widget._move_module(self.widget.entry.section("外貌与辨识特征"), 1)
        self.app.processEvents()
        self.assertEqual(self.name().selectedText(), "留姓名")
        self.assertIs(self.app.focusWidget(), self.name())

    def test_file_watcher_refreshes_clean_and_retains_dirty_document(self):
        changed = character_text().replace("- 姓名：白鸥", "- 姓名：文件监听更新")
        self.path.write_text(changed, encoding="utf-8")
        for _ in range(30):
            QTest.qWait(20)
            if self.name().text() == "文件监听更新":
                break
        self.assertEqual(self.name().text(), "文件监听更新")
        self.name().setText("监听时保留草稿")
        self.path.write_text(changed + "\n再次外部修改\n", encoding="utf-8")
        for _ in range(30):
            QTest.qWait(20)
            if self.widget.external_changed:
                break
        self.assertTrue(self.widget.external_changed)
        self.assertEqual(self.name().text(), "监听时保留草稿")
        self.assertTrue(self.widget.is_modified())

    def test_save_then_edit_handwritten_line_keeps_section_boundaries(self):
        for reopen in (False, True):
            with self.subTest(reopen=reopen):
                source = character_text().replace("## 外貌与辨识特征", "手写备注行\n\n## 外貌与辨识特征")
                self.path.write_text(source, encoding="utf-8")
                self.widget.load_entry(self.path, "character", self.project)
                self.name().setText("已保存的新姓名")
                self.assertEqual(self.widget.write_changes(), (True, ""))
                if reopen:
                    self.widget.load_entry(self.path, "character", self.project)
                rows = next(rows for section, group, rows in self.widget._extras_rows
                            if section.heading == "基础档案" and group is None)
                rows[0].setText("修改后的手写备注")
                self.assertEqual(self.widget.write_changes(), (True, ""))
                saved = self.path.read_text(encoding="utf-8")
                self.assertEqual(saved.count("<!-- novalist:section:v1:start -->"),
                                 saved.count("<!-- novalist:section:v1:end -->"))
                parsed = parse_canon_entry("character", saved)
                self.assertEqual(parsed.section("基础档案").fields["姓名"], "已保存的新姓名")
                self.assertEqual(parsed.section("基础档案").extras, ["修改后的手写备注"])


if __name__ == "__main__":
    unittest.main()
