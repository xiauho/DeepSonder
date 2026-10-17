import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton, QMessageBox
from core.canon_schema import (SECTION_START, SECTION_END, add_module, parse_canon_entry,
    preamble_blocks, raw_section_body, render_canon_entry, rename_module, set_section_text)
from tests.test_canon_schema import character_text
from tests.test_canon_index import FakeProject
from ui.canon_form_widget import CanonFormWidget, NewModuleDialog


class CanonMappingUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = FakeProject(Path(self.tmp.name))
        self.path = self.project.canon_dir / "characters" / "白鸥.md"
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.reset()
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def load(self, source, kind="character"):
        self.path.write_bytes(source.encode("utf-8"))
        widget = CanonFormWidget()
        self.widgets.append(widget)
        widget.load_entry(self.path, kind, self.project)
        return widget

    def test_edit_only_preserves_other_spans_and_eol_then_save_again(self):
        source = character_text().replace("\n", "\r\n")
        widget = self.load(source)
        field = next(e for _s, k, e in widget._field_widgets if k == "姓名")
        field.setText("第一次")
        self.assertEqual(widget.write_changes(), (True, ""))
        saved = self.path.read_bytes()
        self.assertTrue(saved.endswith(source[source.index("## 外貌与辨识特征"):].encode("utf-8")))
        self.assertNotIn(b"\n", saved.replace(b"\r\n", b""))
        field.setText("第二次")
        self.assertEqual(widget.write_changes(), (True, ""))
        before = self.path.read_bytes()
        self.assertEqual(widget.write_changes(), (True, ""))
        self.assertEqual(self.path.read_bytes(), before)

    def test_preamble_edit_keeps_title_bom_and_application_markers(self):
        source = "\ufeff# 标题\r\n\r\n导语一\r\n\r\n<!-- novalist:character-card:v2 -->\r\n\r\n导语二\r\n\r\n## 外貌与辨识特征\r\n\r\n正文\r\n"
        widget = self.load(source)
        self.assertEqual([e.toPlainText() for i, e in widget._preamble_widgets], ["导语一", "导语二"])
        widget._preamble_widgets[0][1].setPlainText("新导语一")
        widget._preamble_widgets[1][1].setPlainText("新导语二")
        self.assertEqual(widget.write_changes(), (True, ""))
        saved = self.path.read_bytes()
        self.assertTrue(saved.startswith("\ufeff# 标题\r\n".encode("utf-8")))
        self.assertIn(b"<!-- novalist:character-card:v2 -->\r\n", saved)
        self.assertTrue(saved.endswith("## 外貌与辨识特征\r\n\r\n正文\r\n".encode("utf-8")))
        widget._preamble_widgets[0][1].setPlainText("再次修改")
        self.assertTrue(widget.write_changes()[0])
        self.assertIn("再次修改", self.path.read_text(encoding="utf-8"))
        self.assertIn("新导语二", self.path.read_text(encoding="utf-8"))

    def test_sectionless_preamble_and_frontmatter_are_editable_safely(self):
        source = "---\rkey: value\r---\r# 标题\r\r只有导语\r"
        widget = self.load(source, "world")
        self.assertEqual(len(widget._preamble_widgets), 1)
        self.assertEqual(widget._preamble_widgets[0][1].toPlainText(), "只有导语")
        widget._preamble_widgets[0][1].setPlainText("修改后的导语")
        self.assertTrue(widget.write_changes()[0])
        self.assertTrue(self.path.read_bytes().startswith(b"---\rkey: value\r---\r"))
        self.assertEqual(widget.current_title(), "标题")

    def test_duplicate_module_buttons_act_on_the_selected_identity(self):
        widget = self.load("# T\n\n## 同名\n\n甲\n\n## 同名\n\n乙\n", "world")
        first, second = widget.entry.sections
        self.assertNotEqual(first.module_id, second.module_id)
        second_editor = widget._text_widgets[1][1]
        second_editor.setPlainText("只修改乙")
        button = next(b for b in widget.findChildren(QPushButton)
                      if b.text() == "重命名" and b.property("moduleId") == second.module_id)
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("第二模块", True)):
            button.click()
        self.assertEqual(widget.entry.sections[0].heading, "同名")
        self.assertEqual(widget.entry.sections[1].heading, "第二模块")
        self.assertTrue(widget.write_changes()[0])
        source = self.path.read_text(encoding="utf-8")
        self.assertTrue(source.startswith("# T\n\n## 同名\n\n甲\n\n"))
        self.assertIn("只修改乙", source)

    def test_relation_rename_keeps_picker_after_save_and_reload(self):
        widget = self.load(character_text())
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("人物交往", True)):
            widget._rename_module(widget.entry.section("关键关系"))
        self.assertTrue(widget._relation_rows)
        identity = widget.entry.section("人物交往").module_id
        self.assertTrue(widget.write_changes()[0])
        widget.load_entry(self.path, "character", self.project)
        self.assertTrue(widget._relation_rows)
        self.assertEqual(widget.entry.section("人物交往").module_id, identity)

    def test_persisted_empty_types_keep_role_and_noop_save_is_byte_identical(self):
        widget = self.load(character_text())
        for role in ("fields", "group"):
            dialog = NewModuleDialog(widget)
            dialog.name_edit.setText("空白" + role)
            dialog.role_buttons[role].setChecked(True)
            dialog.confirm()
        self.assertTrue(widget.write_changes()[0])
        saved = self.path.read_bytes()
        widget.load_entry(self.path, "character", self.project)
        self.assertEqual(widget.entry.section("空白fields").role, "fields")
        self.assertEqual(widget.entry.section("空白group").role, "group")
        self.assertTrue(widget.write_changes()[0])
        self.assertEqual(self.path.read_bytes(), saved)

    def test_suffix_edit_and_module_delete_keep_the_between_note(self):
        source = f"# T\n\n{SECTION_START}\n## 核心规则\n\n规则\n{SECTION_END}\n\n区块间手写备注\n\n## 历史与现状\n\n历史\n"
        widget = self.load(source, "world")
        self.assertEqual(widget._suffix_widgets[0][1].toPlainText(), "区块间手写备注")
        widget._suffix_widgets[0][1].setPlainText("修改后的间隙备注")
        self.assertTrue(widget.write_changes()[0])
        with patch("ui.canon_form_widget.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            widget._delete_module(widget.entry.section("核心规则"))
        self.assertTrue(widget.write_changes()[0])
        self.assertIn("修改后的间隙备注", self.path.read_text(encoding="utf-8"))

    def test_ambiguous_fields_are_editable_as_a_whole_raw_block(self):
        source = "# T\n\n## 基础档案\n\n- 姓名：甲\n- 姓名：乙\n"
        widget = self.load(source)
        self.assertFalse(widget._field_widgets)
        self.assertEqual(len(widget._raw_widgets), 1)
        widget._raw_widgets[0][1].setPlainText("- 姓名：甲\n- 姓名：乙\n补充说明")
        self.assertTrue(widget.write_changes()[0])
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("- 姓名：甲", saved)
        self.assertIn("- 姓名：乙", saved)
        self.assertIn("补充说明", saved)

        before = self.path.read_bytes()
        intro = widget._preamble_widgets[0][1]
        intro.setPlainText("## 不应成为新模块")
        ok, error = widget.write_changes()
        self.assertFalse(ok)
        self.assertIn("标题", error)
        self.assertTrue(widget.is_modified())
        self.assertEqual(intro.toPlainText(), "## 不应成为新模块")
        self.assertEqual(self.path.read_bytes(), before)
        intro.setPlainText("有效导语")
        self.assertTrue(widget.write_changes()[0])

    def test_module_metadata_is_not_displayed_in_raw_editors(self):
        widget = self.load(character_text())
        editor = next(e for s, e in widget._raw_widgets if s.heading == "作者自由备注")
        editor.setPlainText("新的备注")
        self.assertTrue(widget.write_changes()[0])
        widget.load_entry(self.path, "character", self.project)
        editor = next(e for s, e in widget._raw_widgets if s.heading == "作者自由备注")
        self.assertEqual(editor.toPlainText(), "新的备注")
        self.assertNotIn("deepsonder:module", editor.toPlainText())
        self.assertNotIn(SECTION_END, editor.toPlainText())


if __name__ == "__main__":
    unittest.main()
