"""Outline state and navigation use disposable documents and real Qt events."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFontDatabase, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from core.canon_schema import entry_status
from tests.test_canon_index import FakeProject
from tests.test_canon_schema import character_text
from ui.structured_canon_page import StructuredCanonPage
from ui.theme import apply_theme


class CanonOutlineTests(TestCase):
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
        self.tmp = TemporaryDirectory()
        self.project = FakeProject(Path(self.tmp.name))
        self.path = self.project.canon_dir / "characters" / "白鸥.md"
        self.path.write_text(character_text(), encoding="utf-8")
        apply_theme(self.app, {"theme": "light"})
        self.page = StructuredCanonPage()

    def tearDown(self):
        self.page.reset()
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.tmp.cleanup()

    def open(self, *, width=1100, height=600):
        self.page.open_path(self.path, "character", self.project)
        self.page.resize(width, height)
        self.page.show()
        QTest.qWait(60)
        return self.page.form

    def states(self):
        return {heading: filled for _identity, heading, filled in self.page.outline_entries()}

    def test_relations_follow_live_edits_additions_and_last_removal(self):
        form = self.open()
        before = self.path.read_bytes()
        section, editor = form._relation_rows[0]
        editor.clear()
        self.assertFalse(self.states()["关键关系"])
        editor.setText("- 新角色：朋友")
        self.assertTrue(self.states()["关键关系"])
        form._remove_relation_row(editor, editor.parentWidget())
        self.assertFalse(self.states()["关键关系"])
        body = form._module_bodies[section.module_id]
        form._add_relation_row(section, body.layout(), "- 新角色：朋友")
        self.assertTrue(self.states()["关键关系"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_empty_relations_stay_empty_after_save_and_reload(self):
        form = self.open()
        _section, editor = form._relation_rows[0]
        form._remove_relation_row(editor, editor.parentWidget())
        self.assertFalse(self.states()["关键关系"])
        self.assertTrue(form.write_changes()[0])
        form.load_entry(self.path, "character", self.project)
        self.assertFalse(self.states()["关键关系"])

    def test_outline_list_updates_after_relation_edit_without_saving(self):
        form = self.open()
        _section, editor = form._relation_rows[0]
        editor.clear()
        QTest.qWait(360)
        item = next(self.page.outline_list.item(row) for row in range(self.page.outline_list.count())
                    if self.page.outline_list.item(row).text().endswith("关键关系"))
        self.assertTrue(item.text().startswith("○"))
        self.assertIn("空白", item.toolTip())
        self.assertTrue(form.is_modified())

    def test_absent_template_fields_follow_live_input_without_claiming_completeness(self):
        self.path.write_text("# 白鸥\n\n## 基础档案\n\n", encoding="utf-8")
        form = self.open()
        before = self.path.read_bytes()
        editor = next(e for _s, key, e in form._field_widgets if key == "别名")
        self.assertFalse(self.states()["基础档案"])
        editor.setText("林舟")
        self.assertTrue(self.states()["基础档案"])
        self.assertGreater(entry_status(form.entry)["missing_fields"], 0)
        self.page._refresh_outline()
        item = self.page.outline_list.item(1)
        self.assertIn("有内容", item.toolTip())
        self.assertIn("有内容", item.data(Qt.ItemDataRole.AccessibleTextRole))
        editor.setText("   ")
        self.assertFalse(self.states()["基础档案"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_group_fields_not_yet_in_file_follow_live_input(self):
        self.path.write_text("# 白鸥\n\n## 能力档案\n\n### 潮汐感知\n\n", encoding="utf-8")
        form = self.open()
        editor = next(e for _s, _g, key, e in form._group_widgets if key == "使用条件")
        self.assertFalse(self.states()["能力档案"])
        editor.setText("临水")
        self.assertTrue(self.states()["能力档案"])
        editor.clear()
        self.assertFalse(self.states()["能力档案"])

    def test_intro_text_raw_and_handwritten_rows_follow_unsaved_input(self):
        source = character_text().replace("- 首次出场：chapter_01\n", "- 首次出场：chapter_01\n手写说明。\n")
        self.path.write_text(source, encoding="utf-8")
        form = self.open()
        for _section, _key, editor in form._field_widgets:
            editor.clear()
        rows = next(rows for section, group, rows in form._extras_rows
                    if section.heading == "基础档案" and group is None)
        self.assertTrue(self.states()["基础档案"])
        rows[0].clear()
        self.assertFalse(self.states()["基础档案"])
        form._preamble_widgets[0][1].setPlainText("未保存导语")
        self.assertTrue(self.states()["资料导语"])
        form._text_widgets[0][1].clear()
        self.assertFalse(self.states()["外貌与辨识特征"])
        form._raw_widgets[0][1].clear()
        self.assertFalse(self.states()["作者自由备注"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), source)

    def test_bottom_target_stays_selected_until_keyboard_scroll(self):
        form = self.open()
        before = self.path.read_bytes()
        target = form.entry.sections[-1]
        self.assertTrue(form.reveal_module(target.module_id))
        QTest.qWait(80)
        bar = form.form_scroll.verticalScrollBar()
        self.assertEqual(bar.value(), bar.maximum())
        self.assertEqual(self.page._outline_current, target.module_id)
        self.assertEqual(self.page.outline_list.currentItem().data(Qt.ItemDataRole.UserRole), target.module_id)
        toggle = form._module_toggles[target.module_id]
        top = toggle.mapTo(form.form_scroll.viewport(), QPoint(0, 0)).y()
        self.assertGreaterEqual(top, 0)
        self.assertLess(top, form.form_scroll.viewport().height())
        bar.setFocus()
        QTest.keyClick(bar, Qt.Key.Key_Home)
        self.assertEqual(bar.value(), 0)
        self.assertEqual(self.page._outline_current, "")
        self.assertFalse(form.is_modified())
        self.assertEqual(self.path.read_bytes(), before)

    def test_bottom_target_changes_even_when_scroll_position_is_unchanged(self):
        # 留出足够的视口高度，让最后两个模块都受同一个页底滚动上限约束。
        form = self.open(height=800)
        first, second = form.entry.sections[-2:]
        form.reveal_module(first.module_id)
        QTest.qWait(60)
        position = form.form_scroll.verticalScrollBar().value()
        self.assertEqual(position, form.form_scroll.verticalScrollBar().maximum())
        form.reveal_module(second.module_id)
        QTest.qWait(60)
        self.assertEqual(form.form_scroll.verticalScrollBar().value(), position)
        self.assertEqual(self.page._outline_current, second.module_id)

    def test_wheel_scroll_resumes_position_tracking(self):
        form = self.open()
        form.reveal_module(form.entry.sections[-1].module_id)
        QTest.qWait(60)
        viewport = form.form_scroll.viewport()
        local = QPointF(10, 10)
        event = QWheelEvent(local, QPointF(viewport.mapToGlobal(QPoint(10, 10))),
                            QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        self.app.sendEvent(viewport, event)
        self.assertNotEqual(self.page._outline_current, form.entry.sections[-1].module_id)

    def test_collapsed_bottom_target_is_revealed_without_editing(self):
        form = self.open()
        form.set_modules_expanded(False)
        QTest.qWait(60)
        target = form.entry.sections[-1]
        form.reveal_module(target.module_id)
        QTest.qWait(80)
        self.assertTrue(form._module_bodies[target.module_id].isVisible())
        self.assertEqual(self.page._outline_current, target.module_id)
        self.assertFalse(form.is_modified())

    def test_new_document_clears_previous_navigation_target(self):
        form = self.open()
        form.reveal_module(form.entry.sections[-1].module_id)
        QTest.qWait(60)
        another = self.path.with_name("另一份.md")
        another.write_text("# 另一份\n\n## 基础档案\n\n", encoding="utf-8")
        self.page.open_path(another, "character", self.project)
        QTest.qWait(60)
        self.assertEqual(self.page._outline_current, "")
        self.assertEqual(self.page.outline_list.currentRow(), 0)

    def test_narrow_popup_preserves_both_wide_outline_preferences(self):
        self.open()
        before = self.path.read_bytes()
        for requested in (True, False):
            with self.subTest(requested=requested):
                if self.page.outline_toggle.isChecked() != requested:
                    self.page.outline_toggle.click()
                self.page.resize(640, 600)
                QTest.qWait(40)
                with patch("ui.structured_canon_page.QMenu"):
                    self.page.outline_toggle.click()
                self.page.resize(1100, 600)
                QTest.qWait(40)
                self.assertEqual(self.page.outline_panel.isVisible(), requested)
                self.assertEqual(self.page.outline_toggle.isChecked(), requested)
        self.assertFalse(self.page.is_modified())
        self.assertEqual(self.path.read_bytes(), before)

    def test_popup_uses_live_status_and_selects_bottom_target(self):
        form = self.open(width=640)
        _section, editor = form._relation_rows[0]
        editor.clear()

        observed = []

        class SelectingMenu(QMenu):
            def exec(menu, _position):
                relation = next(action for action in menu.actions() if action.text().endswith("关键关系"))
                observed.append((relation.text(), relation.toolTip()))
                menu.actions()[-1].trigger()

        with patch("ui.structured_canon_page.QMenu", SelectingMenu):
            self.page.outline_toggle.click()
        self.assertTrue(observed[0][0].startswith("○"))
        self.assertIn("空白", observed[0][1])
        QTest.qWait(80)
        self.assertEqual(self.page._outline_current, form.entry.sections[-1].module_id)
        self.page.resize(1100, 600)
        QTest.qWait(40)
        self.assertTrue(self.page.outline_panel.isVisible())
