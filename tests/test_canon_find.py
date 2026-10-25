"""Find searches disposable draft controls without mutating author data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit
from core.canon_schema import (add_module, delete_module, move_module, rename_module,
                              SECTION_START, SECTION_END)
from tests.test_canon_index import FakeProject
from tests.test_canon_schema import character_text
from ui.structured_canon_page import StructuredCanonPage
from ui.theme import apply_theme


class CanonFindTests(unittest.TestCase):
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
        self.find = self.page.find_controller
        self.bar = self.page.find_bar
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
        QTest.qWait(35)

    def search(self, text):
        self.page.show_find()
        self.bar.input.setText(text)
        self.find.refresh()
        self.settle()

    def field(self, key="姓名"):
        return next(e for s, k, e in self.form._field_widgets if k == key)

    def text(self, title="外貌与辨识特征"):
        return next(e for s, e in self.form._text_widgets if s.heading == title)

    def test_current_draft_field_names_groups_and_relations_are_searchable(self):
        self.field().setText("草稿中的主名")
        self.form._relation_rows[0][1].setText("- 同伴：草稿中的关系")
        self.search("草稿中的")
        self.assertEqual(len(self.find.hits), 2)
        self.assertIs(self.find._current_target(), self.field())
        self.find.next()
        self.assertIs(self.find._current_target(), self.form._relation_rows[0][1])
        for query, key in (("年龄／出生信息", "field-label"), ("潮汐感知", "group-label"),
                           ("外貌与辨识特征", "heading")):
            self.bar.input.setText(query)
            self.find.refresh()
            self.assertEqual(self.find.current_hit.identity[1][0], key)
            self.assertTrue(self.find._current_target().property("canonFindCurrent"))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_find_does_not_change_history_dirty_state_or_cursor(self):
        self.field().setText("一😀二😀三")
        self.field().setCursorPosition(1)
        before = deepcopy(self.form.history.current)
        size = len(self.form.history.states)
        self.search("😀")
        self.assertEqual(self.field().selectedText(), "😀")
        self.assertEqual(self.field().selectionStart(), 1)
        self.find.next()
        self.assertEqual(self.field().selectionStart(), 4)
        self.page.hide_find()
        self.assertFalse(self.field().hasSelectedText())
        self.assertEqual(self.field().cursorPosition(), 1)
        self.assertEqual(len(self.form.history.states), size)
        self.assertEqual(self.form.history.current, before)
        self.form.edit_command("undo")
        self.assertEqual(self.field().text(), "白鸥")
        self.assertFalse(self.form.is_modified())

    def test_temporary_expansion_does_not_write_fold_preferences(self):
        identity = self.form.entry.section("外貌与辨识特征").module_id
        self.form._module_toggles[identity].setChecked(False)
        save = Mock()
        self.form.collapse_state_saver = save
        self.search("旧疤")
        self.assertTrue(self.form._module_toggles[identity].isChecked())
        self.assertIn(identity, self.form._collapsed_modules())
        self.assertIn(identity, self.form._search_expanded)
        save.assert_not_called()
        self.page.hide_find()
        self.assertFalse(self.form._module_toggles[identity].isChecked())
        save.assert_not_called()
        self.assertFalse(self.form.is_modified())

    def test_user_expansion_and_expand_all_override_temporary_state(self):
        for use_all in (False, True):
            identity = self.form.entry.section("外貌与辨识特征").module_id
            self.form._module_toggles[identity].setChecked(False)
            self.search("旧疤")
            if use_all:
                self.form.set_modules_expanded(True)
            else:
                self.form._module_toggles[identity].setChecked(False)
                self.form._module_toggles[identity].setChecked(True)
            self.page.hide_find()
            self.assertTrue(self.form._module_toggles[identity].isChecked())
            self.assertNotIn(identity, self.form._collapsed_modules())

    def test_next_previous_wrap_and_keyboard_close_clears_highlights(self):
        self.field().setText("echo echo echo")
        self.search("echo")
        self.assertEqual(self.bar.count.text(), "1 / 3")
        QTest.keyClick(self.bar.input, Qt.Key.Key_Return)
        self.assertEqual(self.bar.count.text(), "2 / 3")
        QTest.keyClick(self.bar.input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.bar.count.text(), "1 / 3")
        self.find.previous()
        self.assertEqual(self.bar.count.text(), "3 / 3")
        self.find.next()
        self.assertEqual(self.bar.count.text(), "1 / 3")
        marked = self.find._marked
        QTest.keyClick(self.bar.input, Qt.Key.Key_Escape)
        self.assertFalse(self.bar.isVisible())
        self.assertFalse(marked.property("canonFindCurrent"))
        self.assertIsNone(self.find._marked)

    def test_selected_text_prefills_find_and_empty_and_no_results_are_clear(self):
        editor = self.field()
        editor.setFocus()
        editor.selectAll()
        self.page.show_find()
        self.assertEqual(self.bar.input.text(), "白鸥")
        self.bar.input.setText("不存在的资料内容")
        self.find.refresh()
        self.assertEqual(self.bar.count.text(), "未找到")
        self.assertFalse(self.bar.next.isEnabled())
        self.bar.input.clear()
        self.find.refresh()
        self.assertEqual(self.bar.count.text(), "输入查找内容")
        self.assertEqual(self.find.hits, [])

    def test_literal_case_insensitive_query_and_match_limit(self):
        self.field().setText("[ECHO] [echo] [Echo]")
        self.search("[echo]")
        self.assertEqual(len(self.find.hits), 3)
        self.find.MAX_HITS = 2
        self.find.refresh()
        self.assertEqual(self.bar.count.text(), "1 / 2+")
        self.assertIn("前2处", self.bar.count.toolTip())

    def test_duplicate_module_titles_and_reorder_keep_stable_targets(self):
        source = "# 世界\n\n## 重复标题\n\n唯一甲\n\n## 重复标题\n\n唯一乙\n"
        self.path.write_text(source, encoding="utf-8")
        self.form.load_entry(self.path, "world")
        self.search("重复标题")
        self.assertEqual(len(self.find.hits), 2)
        self.find.next()
        target_id = self.find.current_hit.identity[0]
        self.form._change_structure(lambda d: move_module(d, target_id, -1))
        self.find.refresh()
        self.assertEqual(self.find.current_hit.identity[0], target_id)
        self.assertEqual(self.find.index, 0)
        self.find.next()
        self.assertNotEqual(self.find.current_hit.identity[0], target_id)
        self.assertEqual(self.find.index, 1)

    def test_structure_delete_undo_and_rebuild_never_use_destroyed_widgets(self):
        self.search("外貌与辨识特征")
        identity = self.find.current_hit.identity[0]
        self.form._change_structure(lambda d: rename_module(d, identity, "外貌与辨识特征·新"))
        self.find.refresh()
        self.assertEqual(len(self.find.hits), 1)
        self.form._change_structure(lambda d: delete_module(d, identity))
        self.find.refresh()
        self.assertEqual(len(self.find.hits), 0)
        self.form.edit_command("undo")
        self.find.refresh()
        self.find.next()
        self.assertEqual(self.find.current_hit.identity[0], identity)
        self.assertTrue(self.find._current_target().isVisible())

    def test_temporary_expansion_survives_rebuild_without_becoming_preference(self):
        identity = self.form.entry.section("外貌与辨识特征").module_id
        self.form._module_toggles[identity].setChecked(False)
        self.search("旧疤")
        self.form._change_structure(lambda d: add_module(d, "新模块", "text"))
        self.find.refresh()
        self.assertTrue(self.form._module_toggles[identity].isChecked())
        self.page.hide_find()
        self.assertFalse(self.form._module_toggles[identity].isChecked())

    def test_raw_preamble_extras_and_suffix_are_all_searchable(self):
        source = (f"# T\n\n共同导语\n\n{SECTION_START}\n## 核心规则\n\n规则\n{SECTION_END}\n\n共同备注\n\n"
                  "## 字段\n\n- A：值\n共同手写行\n\n## 表格\n\n|共同原文|\n|---|\n")
        self.path.write_text(source, encoding="utf-8")
        self.form.load_entry(self.path, "world")
        self.search("共同")
        self.assertEqual(len(self.find.hits), 4)
        self.assertEqual([hit.identity[1][0] for hit in self.find.hits], ["intro", "suffix", "extras", "raw"])
        self.assertFalse(self.form.is_modified())

    def test_ime_preedit_is_not_a_query_until_committed(self):
        self.search("")
        self.app.sendEvent(self.bar.input, QInputMethodEvent("白", []))
        self.assertEqual(self.bar.input.text(), "")
        self.assertEqual(self.find.hits, [])
        event = QInputMethodEvent()
        event.setCommitString("白鸥")
        self.app.sendEvent(self.bar.input, event)
        self.find.refresh()
        self.assertGreater(len(self.find.hits), 0)
        self.assertIs(self.app.focusWidget(), self.bar.input)

    def test_long_text_reveals_internal_cursor_and_uses_extra_highlight(self):
        editor = self.text()
        editor.setPlainText("\n".join("普通正文" for _ in range(80)) + "\n末尾命中")
        self.search("末尾命中")
        self.assertGreater(editor.verticalScrollBar().value(), 0)
        self.assertEqual(len(editor.extraSelections()), 1)
        rect = editor.cursorRect()
        self.assertTrue(editor.viewport().rect().intersects(rect))
        self.page.hide_find()
        self.assertEqual(editor.extraSelections(), [])

    def test_readonly_sync_values_search_display_without_control_markers(self):
        self.search("北港")
        self.assertEqual(self.find.current_hit.identity[1][0], "sync-value")
        self.assertTrue(self.find._current_target().property("canonFindCurrent"))
        self.bar.input.setText("novalist:auto-state")
        self.find.refresh()
        self.assertEqual(self.find.hits, [])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_editing_a_hit_refreshes_results_without_losing_new_cursor(self):
        self.field().setText("echo echo")
        self.search("echo")
        editor = self.field()
        editor.setFocus()
        QTest.keyClicks(editor, "new")
        position = editor.cursorPosition()
        self.find.refresh()
        self.assertEqual(editor.text(), "new echo")
        self.assertEqual(editor.cursorPosition(), position)
        self.assertFalse(editor.hasSelectedText())
        self.assertEqual(len(self.find.hits), 1)
        self.page.hide_find()
        self.assertEqual(editor.text(), "new echo")
        self.form.edit_command("undo")
        self.assertEqual(self.field().text(), "echo echo")

    def test_refresh_does_not_move_active_multiline_editor_cursor(self):
        editor = self.text()
        editor.setPlainText("echo echo")
        self.search("echo")
        editor.setFocus()
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        editor.insertPlainText(" new")
        position = editor.textCursor().position()
        self.find.refresh()
        self.assertEqual(editor.textCursor().position(), position)
        self.page.hide_find()
        self.assertEqual(editor.textCursor().position(), position)

    def test_document_switch_reset_and_pending_refresh_clear_query(self):
        self.search("旧疤")
        self.bar.input.setText("待执行查询")
        other = self.path.with_name("另一份.md")
        other.write_bytes(self.original)
        self.form.load_entry(other, "character", self.project)
        self.settle()
        self.assertFalse(self.bar.isVisible())
        self.assertEqual(self.bar.input.text(), "")
        self.assertEqual(self.find.hits, [])
        self.assertFalse(self.find._timer.isActive())
        self.search("旧疤")
        self.form.reset()
        self.settle()
        self.assertFalse(self.bar.isVisible())
        self.assertEqual(self.find.hits, [])

    def test_find_bar_reflows_without_horizontal_overflow_in_both_themes(self):
        for theme in ("light", "dark"):
            apply_theme(self.app, {"theme": theme, "ui_font_size": 20})
            self.page.set_theme({"theme": theme})
            for width in (520, 1100):
                self.page.resize(width, 800)
                self.search("旧疤")
                self.settle()
                self.assertTrue(self.bar.rect().contains(self.bar.close.mapTo(self.bar, self.bar.close.rect().bottomRight())))
                self.assertEqual(self.form.form_scroll.horizontalScrollBar().maximum(), 0)
                editor = self.find._current_target()
                top = editor.viewport().mapTo(self.form.form_scroll.viewport(), editor.cursorRect().topLeft()).y()
                self.assertGreaterEqual(top, 0)
                self.assertLess(top + editor.cursorRect().height(), self.form.form_scroll.viewport().height())
                self.assertFalse(self.form.is_modified())


if __name__ == "__main__":
    unittest.main()
