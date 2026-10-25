"""Responsive canon layouts keep the same input objects, draft and history."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tests.test_canon_schema import character_text
from ui.canon_layout import CanonFieldsGrid
from ui.structured_canon_page import StructuredCanonPage
from ui.theme import apply_theme


class CanonDensityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = Path("C:/Windows/Fonts/msyh.ttc")
        cls.font_id = QFontDatabase.addApplicationFont(str(font)) if font.exists() else -1

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        apply_theme(self.app, {"theme": "light", "ui_font_size": 14})
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "character.md"
        self.original = ("\ufeff" + character_text().replace("\n", "\r\n")).encode("utf-8")
        self.path.write_bytes(self.original)
        self.page = StructuredCanonPage()
        self.page.open_path(self.path, "character")
        self.form = self.page.form
        self.page.resize(1100, 850)
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
        QTest.qWait(70)

    def field(self, key="姓名"):
        return next(e for s, k, e in self.form._field_widgets if k == key)

    def grid(self):
        return self.form.findChild(CanonFieldsGrid)

    def positions(self):
        grid = self.grid()
        return {editor.accessibleName(): grid.layout().getItemPosition(grid.layout().indexOf(field))
                for field, _label, editor, _short in grid._fields}

    def test_short_fields_pair_and_semantic_long_field_spans_whole_row(self):
        positions = self.positions()
        self.assertEqual(positions["姓名"][0], positions["别名"][0])
        self.assertEqual(positions["姓名"][1], 0)
        self.assertEqual(positions["别名"][1], 1)
        self.assertEqual(positions["身份／阵营"][3], 2)
        self.assertEqual(positions["首次出场"][3], 2)
        self.assertFalse(self.form.is_modified())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_width_and_font_changes_keep_widgets_focus_selection_and_history(self):
        editor = self.field()
        editor.setText("甲😀乙")
        editor.setFocus()
        editor.setSelection(1, 2)
        history_size = len(self.form.history.states)
        widgets = self.form._editable_widgets()
        for theme, width, font in (("dark", 520, 20), ("light", 1100, 14), ("dark", 1100, 26)):
            apply_theme(self.app, {"theme": theme, "ui_font_size": font})
            self.page.resize(width, 850)
            self.settle()
            self.assertIs(self.field(), editor)
            self.assertEqual(self.form._editable_widgets(), widgets)
            self.assertIs(self.app.focusWidget(), editor)
            self.assertEqual(editor.selectedText(), "😀")
            self.assertEqual(len(self.form.history.states), history_size)
            if width == 520 or font == 26:
                self.assertTrue(all(pos[3] == 2 for pos in self.positions().values()))
            self.assertEqual(self.form.form_scroll.horizontalScrollBar().maximum(), 0)
        self.form.edit_command("undo")
        self.assertEqual(self.field().text(), "白鸥")
        self.assertFalse(self.form.is_modified())

    def test_reflow_preserves_row_major_tab_order(self):
        for width in (520, 1100, 520):
            self.page.resize(width, 850)
            self.settle()
            editor = self.field()
            editor.setFocus()
            for expected in ("别名", "年龄／出生信息", "性别", "身份／阵营", "首次出场"):
                QTest.keyClick(self.app.focusWidget(), Qt.Key.Key_Tab)
                self.assertIs(self.app.focusWidget(), self.field(expected))

    def test_long_draft_expands_to_full_row_and_undo_reflows_same_input(self):
        editor = self.field("别名")
        editor.setText("非常长的角色称呼" * 20)
        self.settle()
        self.assertEqual(self.positions()["别名"][3], 2)
        self.form.edit_command("undo")
        self.settle()
        self.assertIs(self.field("别名"), editor)
        self.assertEqual(self.positions()["别名"][3], 1)

    def test_available_grid_width_controls_columns_when_outline_is_toggled(self):
        self.page.resize(980, 850)
        self.settle()
        narrow = self.grid().width()
        self.page.outline_toggle.click()
        self.settle()
        self.assertGreater(self.grid().width(), narrow)
        self.assertIs(self.field(), self.form._field_widgets[0][2])
        self.assertFalse(self.form.is_modified())

    def test_empty_intro_is_compact_and_button_does_not_edit_document(self):
        intro = self.form._intro_card
        editor = self.form._preamble_widgets[0][1]
        self.assertFalse(editor.isVisible())
        self.assertTrue(intro.toggle.isVisible())
        before = intro.height()
        steps = len(self.form.history.states)
        intro.toggle.click()
        self.settle()
        self.assertTrue(editor.isVisible())
        self.assertIs(self.app.focusWidget(), editor)
        self.assertGreater(intro.height(), before)
        intro.toggle.click()
        self.settle()
        self.assertFalse(editor.isVisible())
        self.assertEqual(len(self.form.history.states), steps)
        self.form.reveal_module("")
        self.settle()
        self.assertTrue(editor.isVisible())
        self.assertFalse(self.form.is_modified())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_existing_intro_visible_and_clearing_does_not_hide_active_input(self):
        source = character_text().replace("# 白鸥\n", "# 白鸥\n\n已有导语\n", 1)
        self.path.write_text(source, encoding="utf-8")
        self.form.load_entry(self.path, "character")
        self.settle()
        editor = self.form._preamble_widgets[0][1]
        self.assertTrue(editor.isVisible())
        self.assertFalse(self.form._intro_card.toggle.isVisible())
        editor.setFocus()
        editor.clear()
        self.settle()
        self.assertTrue(editor.isVisible())
        self.assertTrue(self.form._intro_card.toggle.isVisible())
        self.form.edit_command("undo")
        self.settle()
        self.assertIn("已有导语", editor.toPlainText())
        self.assertFalse(self.form._intro_card.toggle.isVisible())

    def test_intro_history_restores_visible_target_after_empty_collapse(self):
        self.form._intro_card.toggle.click()
        editor = self.form._preamble_widgets[0][1]
        editor.setPlainText("导语草稿")
        self.form.edit_command("undo")
        self.settle()
        self.form._intro_card.toggle.click()
        self.settle()
        self.assertFalse(editor.isVisible())
        self.form.edit_command("redo")
        self.settle()
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.toPlainText(), "导语草稿")

    def test_find_after_reflow_keeps_actual_match_visible(self):
        self.field().setText("命中名字")
        self.page.show_find()
        self.page.find_bar.input.setText("命中")
        self.page.find_controller.refresh()
        for width in (520, 1100, 520):
            self.page.resize(width, 850)
            self.settle()
            editor = self.page.find_controller._current_target()
            self.assertIs(editor, self.field())
            top = editor.mapTo(self.form.form_scroll.viewport(), editor.rect().topLeft()).y()
            self.assertGreaterEqual(top, 0)
            self.assertLess(top + editor.height(), self.form.form_scroll.viewport().height())
            self.assertEqual(editor.selectedText(), "命中")


if __name__ == "__main__":
    unittest.main()
