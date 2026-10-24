"""Document history uses temporary projects and never modifies real author data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.canon_schema import (add_module, delete_module, rename_module, move_module,
                               SECTION_START, SECTION_END)
from core.canon_fields import (add_field, delete_field, rename_field, move_field,
                               add_group, delete_group, rename_group, move_group)
from tests.test_canon_index import FakeProject
from tests.test_canon_schema import character_text
from ui.canon_history import CanonEditHistory, CanonSnapshot
from ui.structured_canon_page import StructuredCanonPage


class HistoryBudgetTests(unittest.TestCase):
    def state(self, text):
        return CanonSnapshot(None, {"text": text}, {})

    def test_step_limit_and_new_branch(self):
        history = CanonEditHistory()
        history.MAX_STEPS = 3
        history.clear(self.state("0"))
        for i in range(1, 8):
            history.record(self.state(str(i)))
        self.assertEqual(len(history.states), 4)
        for _ in range(3): history.step(-1)
        self.assertEqual(history.current.values["text"], "4")
        self.assertIsNone(history.step(-1))
        history.record(self.state("new"))
        self.assertFalse(history.can_redo)

    def test_memory_limit_never_truncates_current_input(self):
        history = CanonEditHistory()
        history.MAX_BYTES = 3000
        history.clear(self.state("start"))
        history.record(self.state("x" * 10000))
        self.assertEqual(len(history.states), 1)
        self.assertEqual(len(history.current.values["text"]), 10000)
        self.assertFalse(history.can_undo)
        history.MAX_BYTES = 100000
        history.record(self.state("small"))
        self.assertEqual(history.step(-1).values["text"], "x" * 10000)

    def test_merge_timeout_and_explicit_boundary(self):
        history = CanonEditHistory()
        history.clear(self.state(""))
        with patch("ui.canon_history.monotonic", side_effect=[0, .1, 2, 2.1]):
            history.record(self.state("a"), "editor")
            history.record(self.state("ab"), "editor")
            history.record(self.state("abc"), "editor")
            history.break_merge()
            history.record(self.state("abcd"), "editor")
        self.assertEqual(history.step(-1).values["text"], "abc")
        self.assertEqual(history.step(-1).values["text"], "ab")
        self.assertEqual(history.step(-1).values["text"], "")


class CanonHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.project = FakeProject(Path(self.tmp.name))
        self.path = self.project.canon_dir / "characters" / "白鸥.md"
        self.original = ("\ufeff" + character_text().replace("\n", "\r\n")).encode("utf-8")
        self.path.write_bytes(self.original)
        self.page = StructuredCanonPage()
        self.page.open_path(self.path, "character", self.project)
        self.form = self.page.form
        self.page.resize(1100, 800)
        self.page.show()
        self.settle()

    def tearDown(self):
        self.page.reset()
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.tmp.cleanup()

    def settle(self):
        QTest.qWait(30)

    def field(self, key="姓名"):
        return next(e for s, k, e in self.form._field_widgets if k == key)

    def text(self, title="外貌与辨识特征"):
        return next(e for s, e in self.form._text_widgets if s.heading == title)

    def command(self, direction):
        self.form.edit_command(direction)
        self.settle()

    def test_typing_across_fields_uses_one_chronological_history(self):
        name = self.field()
        name.setFocus()
        QTest.keyClick(name, Qt.Key.Key_End)
        QTest.keyClicks(name, "abc")
        alias = self.field("别名")
        alias.setFocus()
        QTest.keyClick(alias, Qt.Key.Key_End)
        QTest.keyClicks(alias, "xyz")
        self.command("undo")
        self.assertEqual(alias.text(), "林舟")
        self.assertEqual(name.text(), "白鸥abc")
        self.command("undo")
        self.assertEqual(name.text(), "白鸥")
        self.assertFalse(self.form.is_modified())
        self.assertEqual(self.path.read_bytes(), self.original)
        self.command("redo")
        self.command("redo")
        self.assertEqual(alias.text(), "林舟xyz")

    def test_replacement_and_continuous_deletion_have_useful_boundaries(self):
        editor = self.field()
        editor.setFocus()
        editor.selectAll()
        QTest.keyClicks(editor, "replacement")
        self.command("undo")
        self.assertEqual(self.field().text(), "白鸥")
        self.command("redo")
        for _ in range(3): QTest.keyClick(self.field(), Qt.Key.Key_Backspace)
        self.command("undo")
        self.assertEqual(self.field().text(), "replacement")
        self.command("undo")
        self.assertEqual(self.field().text(), "白鸥")

    def test_noop_structure_and_failed_save_keep_history_and_selection(self):
        editor = self.field()
        editor.setText("白😀鸥")
        editor.setFocus()
        editor.setSelection(1, 2)
        identity = self.form.entry.section("外貌与辨识特征").module_id
        count = len(self.form.history.states)
        self.form._change_structure(lambda d: rename_module(d, identity, "外貌与辨识特征"))
        self.assertEqual(len(self.form.history.states), count)
        self.assertIs(self.field(), editor)
        self.form._change_structure(lambda d: rename_module(d, identity, "新外貌"))
        self.assertEqual(self.field().selectedText(), "😀")
        with patch("ui.canon_form_widget.atomic_write_text", side_effect=OSError("拒绝写入")):
            self.assertFalse(self.form.write_changes()[0])
        self.command("undo")
        self.assertEqual(self.field().selectedText(), "😀")
        self.assertEqual(self.page.modified_badge.text(), "保存失败")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_clean_external_reload_clears_old_revision_history(self):
        self.field().setText("saved")
        self.form.write_changes()
        self.assertTrue(self.form.history.can_undo)
        external = self.path.read_bytes() + "\n外部新增".encode("utf-8")
        self.path.write_bytes(external)
        self.assertTrue(self.form.refresh_from_disk())
        self.assertFalse(self.form.history.can_undo)
        self.command("undo")
        self.assertEqual(self.path.read_bytes(), external)

    def test_native_shortcuts_and_context_menu_use_document_order(self):
        self.field().setText("first")
        self.field("别名").setText("second")
        editor = self.field()
        editor.setFocus()
        QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.field("别名").text(), "林舟")
        QTest.keyClick(self.field("别名"), Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.field("别名").text(), "second")
        menu = self.form._make_editor_menu(editor)
        self.assertTrue(menu.actions()[0].isEnabled())
        menu.actions()[0].trigger()
        menu.deleteLater()
        self.assertEqual(self.field("别名").text(), "林舟")
        self.assertEqual(editor.text(), "first")

    def test_text_structure_and_autosave_can_be_reversed_in_order(self):
        self.page.configure_auto_save(True, 30)
        self.field().setText("未保存姓名")
        base = self.form.entry.section("基础档案").module_id
        self.form._change_structure(lambda d: add_field(d, base, "偏好"))
        self.form._change_structure(lambda d: rename_field(d, base, "偏好", "爱好"))
        appearance = self.form.entry.section("外貌与辨识特征").module_id
        self.form._change_structure(lambda d: delete_module(d, appearance))
        final = self.form._effective_text()
        self.page._auto_save_flush()
        saved = self.path.read_bytes()
        self.assertFalse(self.form.is_modified())
        self.command("undo")
        self.assertEqual(self.form.entry.sections[1].module_id, appearance)
        self.assertTrue(self.form._module_toggles[appearance].isChecked())
        self.assertTrue(self.form.is_modified())
        self.assertTrue(self.page._auto_save_timer.isActive())
        self.command("undo")
        self.assertIn("偏好", self.form.entry.section("基础档案").fields)
        self.command("undo")
        self.assertNotIn("偏好", self.form.entry.section("基础档案").fields)
        self.assertEqual(self.field().text(), "未保存姓名")
        self.command("undo")
        self.assertEqual(self.form._effective_text().encode("utf-8"), self.original)
        self.assertEqual(self.path.read_bytes(), saved)
        for _ in range(4): self.command("redo")
        self.assertEqual(self.form._effective_text(), final)
        self.assertFalse(self.form.is_modified())

    def test_rename_move_delete_restore_position_and_focused_selection(self):
        editor = self.text()
        editor.setFocus()
        cursor = editor.textCursor()
        cursor.setPosition(1)
        cursor.setPosition(4, cursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        identity = self.form.entry.section("外貌与辨识特征").module_id
        self.form._change_structure(lambda d: rename_module(d, identity, "新外貌"))
        self.form._change_structure(lambda d: move_module(d, identity, 1))
        self.form._module_toggles[identity].setChecked(False)
        self.form._change_structure(lambda d: delete_module(d, identity))
        self.command("undo")
        editor = self.text("新外貌")
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.textCursor().selectedText(), "手腕有")
        self.command("undo")
        self.assertEqual(self.form.entry.sections[1].module_id, identity)
        self.command("undo")
        self.assertEqual(self.form.entry.sections[1].heading, "外貌与辨识特征")

    def test_field_and_group_order_and_values_are_restored(self):
        identity = self.form.entry.section("基础档案").module_id
        steps = [lambda d: add_field(d, identity, "甲"),
                 lambda d: add_field(d, identity, "乙"),
                 lambda d: move_field(d, identity, "乙", -1),
                 lambda d: delete_field(d, identity, "甲")]
        group_id = self.form.entry.section("能力档案").module_id
        steps += [lambda d: add_group(d, group_id, "新能力"),
                  lambda d: rename_group(d, group_id, "潮汐感知", "感潮"),
                  lambda d: move_group(d, group_id, "感潮", 1),
                  lambda d: delete_group(d, group_id, "感潮")]
        states = [self.form._effective_text()]
        for step in steps:
            self.form._change_structure(step)
            states.append(self.form._effective_text())
        for expected in reversed(states[:-1]):
            self.command("undo")
            self.assertEqual(self.form._effective_text(), expected)
        for expected in states[1:]:
            self.command("redo")
            self.assertEqual(self.form._effective_text(), expected)

    def test_relations_add_delete_last_row_and_text_have_independent_steps(self):
        editor = self.form._relation_rows[0][1]
        editor.setText("- 守塔老人：朋友")
        self.form._remove_relation_row(editor, editor.parentWidget())
        self.assertEqual(self.form._relation_rows, [])
        self.command("undo")
        self.assertEqual(self.form._relation_rows[0][1].text(), "- 守塔老人：朋友")
        self.command("undo")
        self.assertEqual(self.form._relation_rows[0][1].text(), "- 守塔老人：师徒")
        self.command("redo")
        self.command("redo")
        self.assertEqual(self.form._relation_rows, [])
        with patch.object(self.form, "_relation_candidates", return_value=["同伴"]), patch(
                "ui.canon_form_widget.QInputDialog.getItem", return_value=("同伴", True)):
            section = self.form.entry.section("关键关系")
            self.form._pick_relation(section, self.form._module_bodies[section.module_id].layout())
        self.assertEqual(self.form._relation_rows[0][1].text(), "- 同伴：")
        self.command("undo")
        self.assertEqual(self.form._relation_rows, [])

    def test_undo_does_not_overwrite_external_revision_or_hide_conflict(self):
        self.field().setText("local")
        external = self.original + "\n外部版本".encode("utf-8")
        self.path.write_bytes(external)
        self.assertFalse(self.form.refresh_from_disk())
        self.command("undo")
        self.assertEqual(self.path.read_bytes(), external)
        self.assertTrue(self.form.external_changed)
        self.assertEqual(self.page.modified_badge.text(), "外部版本变化")
        self.assertFalse(self.form.write_changes()[0])
        self.command("redo")
        self.assertEqual(self.field().text(), "local")

    def test_invalid_intermediate_input_and_failed_structure_are_undoable(self):
        editor = self.text()
        editor.setPlainText("```未闭合")
        count = len(self.form.history.states)
        with self.assertRaises(ValueError):
            self.form._change_structure(lambda d: add_module(d, "新模块", "text"))
        self.assertEqual(len(self.form.history.states), count)
        self.assertFalse(self.form.write_changes()[0])
        self.command("undo")
        self.assertEqual(self.text().toPlainText(), "左手腕有旧疤。")
        self.command("redo")
        self.assertEqual(self.text().toPlainText(), "```未闭合")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_ime_commit_and_multiline_input(self):
        editor = self.text()
        editor.setFocus()
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        event = QInputMethodEvent()
        event.setCommitString("中文输入")
        self.app.sendEvent(editor, event)
        self.command("undo")
        self.assertNotIn("中文输入", self.text().toPlainText())
        self.command("redo")
        self.assertIn("中文输入", self.text().toPlainText())
        self.text().insertPlainText("\n第二行\n第三行")
        self.command("undo")
        self.assertNotIn("第三行", self.text().toPlainText())

    def test_new_edit_after_undo_clears_redo_and_save_keeps_it(self):
        self.field().setText("one")
        self.field().setText("two")
        self.command("undo")
        self.assertTrue(self.form.write_changes()[0])
        self.assertTrue(self.form.history.can_redo)
        self.command("redo")
        self.assertEqual(self.field().text(), "two")
        self.command("undo")
        self.field().setText("branch")
        self.assertFalse(self.form.history.can_redo)
        self.command("redo")
        self.assertEqual(self.field().text(), "branch")

    def test_reload_switch_discard_and_reset_clear_history(self):
        self.field().setText("saved")
        self.form.write_changes()
        self.assertTrue(self.form.history.can_undo)
        self.assertTrue(self.page.reload_from_disk())
        self.assertFalse(self.form.history.can_undo)
        self.field().setText("discard")
        with patch("ui.canon_form_widget.QMessageBox") as box:
            buttons = [object(), object(), object()]
            box.return_value.addButton.side_effect = buttons
            box.return_value.clickedButton.return_value = buttons[1]
            self.assertTrue(self.form.confirm_discard_or_save())
        self.assertFalse(self.form.history.can_undo)
        other = self.path.with_name("另一份.md")
        other.write_bytes(self.original)
        self.field().setText("switch")
        self.form.load_entry(other, "character", self.project)
        self.command("undo")
        self.assertEqual(self.field().text(), "白鸥")
        self.form.reset()
        self.assertFalse(self.form.history.can_redo)

    def test_preamble_raw_suffix_and_extras_are_in_document_history(self):
        source = (f"# 世界\n\n导语\n\n{SECTION_START}\n## 核心规则\n\n规则\n{SECTION_END}\n\n间隙备注\n\n"
                  "## 自定义字段\n\n- A：值\n手写行\n\n## 自由\n\n|原文|\n|---|\n")
        self.path.write_text(source, encoding="utf-8")
        self.form.load_entry(self.path, "world")
        original = self.form._effective_text()
        self.form._preamble_widgets[0][1].setPlainText("新导语")
        self.form._extras_rows[0][2][0].setText("新手写行")
        self.form._raw_widgets[0][1].setPlainText("|修改原文|")
        self.form._suffix_widgets[0][1].setPlainText("新的间隙备注")
        for _ in range(4): self.command("undo")
        self.assertEqual(self.form._effective_text(), original)
        for _ in range(4): self.command("redo")
        self.assertIn("新导语", self.form._effective_text())
        self.assertIn("新手写行", self.form._effective_text())
        self.assertIn("修改原文", self.form._effective_text())
        self.assertIn("新的间隙备注", self.form._effective_text())


if __name__ == "__main__":
    unittest.main()
