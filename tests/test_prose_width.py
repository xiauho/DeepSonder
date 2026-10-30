"""Reading widths are display preferences, never document edits or saves."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QFontDatabase, QMouseEvent, QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from core.expansion import ExpansionRunResult
from core.task_context import AIContextSnapshot
from ui.main_window import MainWindow
from ui.prose_width import reading_width_preferences


class ProseWidthTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        path = Path("C:/Windows/Fonts/msyh.ttc")
        cls.font_id = QFontDatabase.addApplicationFont(str(path)) if path.exists() else -1

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        restore = patch.object(MainWindow, "_restore_last_project")
        restore.start()
        self.addCleanup(restore.stop)
        self.project = NovelProject.create(Path(self.tmp.name) / "book", "行宽调整")
        self.project.save_chapter("chapter_01", "找到灯塔", "旧站的灯还亮着。\n\n" * 100, title="旧站")
        self.path = self.project.chapters_dir / "chapter_01.md"
        self.original = self.path.read_bytes()
        self.profile = Path(self.tmp.name) / "ui.json"
        self.windows = []
        self.addCleanup(self.close_windows)
        self.window = self.make_window()
        self.page = self.window.editor.writing_page
        self.edit = self.page.body_edit

    def make_window(self):
        window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False}, ui_state_path=self.profile)
        self.windows.append(window)
        window.resize(1600, 900)
        window.show()
        window.project_session.set_project(self.project)
        window._show_route("writing")
        window._on_file_selected("章节", str(self.path))
        self.settle()
        return window

    def close_windows(self):
        for window in reversed(self.windows):
            window.ai_workflow_controller.discard_pending_result()
            window.review_panel.finish()
            window.editor._set_dirty(False)
            window.structured_canon_page.reset()
            window.settings_page.set_config(window.config)
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def settle(self):
        QTest.qWait(25)
        self.app.processEvents()

    def start_drag(self, side, delta):
        handle = self.page.width_handles[side]
        point = handle.rect().center()
        start = handle.mapToGlobal(point)
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
        end = start + QPoint(delta, 0)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(handle.mapFromGlobal(end)), QPointF(end),
                            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(handle, event)
        return handle, end

    def drag(self, side, delta):
        handle, end = self.start_drag(side, delta)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(end))
        self.settle()

    def test_both_drag_edges_preserve_draft_selection_undo_revision_and_ai_context(self):
        w = self.window
        self.edit.moveCursor(QTextCursor.MoveOperation.End)
        self.edit.insertPlainText("作者的未保存草稿。")
        cursor = self.edit.textCursor()
        cursor.setPosition(24)
        cursor.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
        self.edit.setTextCursor(cursor)
        self.edit.setFocus()
        self.settle()
        source = w.editor.document_text()
        revision = w.editor.loaded_revision()
        undo = self.edit.document().availableUndoSteps()
        context = AIContextSnapshot.capture(self.project, "chapter_01", source, task_kind="expand")
        changed = QSignalSpy(self.page.changed)
        saved = QSignalSpy(w.editor.file_saved)
        for side, delta in (("right", -35), ("left", 45)):
            before = self.edit.width() - 64
            self.drag(side, delta)
            expected = before + delta * (2 if side == "right" else -2)
            self.assertEqual(self.page.reading_width_preferences(), {"mode": "custom", "custom_width": max(480, expected)})
            self.assertIs(w.focusWidget(), self.edit)
            self.assertEqual((self.edit.textCursor().position(), self.edit.textCursor().anchor()), (5, 24))
            self.assertEqual(self.edit.document().availableUndoSteps(), undo)
            self.assertEqual(w.editor.loaded_revision(), revision)
            self.assertEqual(w.editor.document_text(), source)
            self.assertTrue(context.matches(self.project, "chapter_01", w.editor.document_text()))
        self.assertEqual((changed.count(), saved.count()), (0, 0))
        self.assertEqual(self.path.read_bytes(), self.original)
        self.edit.undo()
        self.assertNotIn("作者的未保存草稿。", self.edit.toPlainText())

    def test_modes_and_numeric_entry_reuse_actions_and_remember_custom_width(self):
        w = self.window
        with patch("ui.main_window.QInputDialog.getInt", return_value=(920, True)):
            w.actions["width_value"].trigger()
        self.assertEqual(self.page.reading_width_preferences(), {"mode": "custom", "custom_width": 920})
        self.assertTrue(w.actions["width_custom"].isChecked())
        w.actions["width_fill"].trigger()
        self.assertEqual(self.page.layout().contentsMargins().left(), 0)
        w.actions["width_recommended"].trigger()
        w.actions["width_custom"].trigger()
        self.assertEqual(self.page.reading_width_preferences()["custom_width"], 920)
        for key in ("width_recommended", "width_custom", "width_fill", "width_value", "width_reset"):
            self.assertIn(w.actions[key], w.prose_width_menu.actions())
            entry = next(item for item in w.quick_access_controller.entries() if item.key == key)
            self.assertTrue(entry.detail.startswith("视图与导航"))
        w.actions["width_reset"].trigger()
        self.assertEqual(self.page.reading_width_preferences()["mode"], "recommended")
        self.assertFalse(w.editor.is_dirty())

    def test_click_enter_space_and_cancel_offer_non_drag_input(self):
        handle = self.page.width_handles["right"]
        before = self.page.reading_width_preferences()
        with patch("ui.main_window.QInputDialog.getInt", return_value=(1000, False)) as dialog:
            QTest.mouseClick(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
            QTest.keyClick(handle, Qt.Key.Key_Return)
            QTest.keyClick(handle, Qt.Key.Key_Space)
        self.assertEqual(dialog.call_count, 3)
        self.assertEqual(self.page.reading_width_preferences(), before)
        self.assertFalse(self.window.editor.is_dirty())

    def test_keyboard_edges_and_home_restore_recommended(self):
        self.page.set_reading_width("custom", 760)
        right, left = self.page.width_handles["right"], self.page.width_handles["left"]
        right.setFocus()
        QTest.keyClick(right, Qt.Key.Key_Right)
        self.assertEqual(self.page.reading_width_preferences()["custom_width"], 776)
        QTest.keyClick(left, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.page.reading_width_preferences()["custom_width"], 712)
        QTest.keyClick(left, Qt.Key.Key_Home)
        self.assertEqual(self.page.reading_width_preferences()["mode"], "recommended")
        self.assertTrue(right.accessibleName())
        self.assertEqual(right.focusPolicy(), Qt.FocusPolicy.TabFocus)

    def test_escape_cancels_drag_before_exiting_focus_mode(self):
        self.page.set_reading_width("custom", 840)
        self.window.actions["focus"].trigger()
        self.settle()
        before = self.page.reading_width_preferences()
        handle, end = self.start_drag("right", -50)
        self.assertNotEqual(self.page.reading_width_preferences(), before)
        QTest.keyClick(self.edit, Qt.Key.Key_Escape)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(end))
        self.assertEqual(self.page.reading_width_preferences(), before)
        self.assertTrue(self.window.window_state_controller.focus_mode)
        QTest.keyClick(self.edit, Qt.Key.Key_Escape)
        self.assertFalse(self.window.window_state_controller.focus_mode)

    def test_leaving_page_cancels_unfinished_drag(self):
        before = self.page.reading_width_preferences()
        handle, end = self.start_drag("right", -50)
        self.window._show_route("reports")
        self.settle()
        self.assertFalse(handle._dragging)
        self.assertEqual(self.page.reading_width_preferences(), before)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(end))

    def test_lost_mouse_capture_or_window_deactivation_cancels_drag(self):
        for target, kind in ((self.page.width_handles["right"], QEvent.Type.UngrabMouse),
                             (self.window, QEvent.Type.WindowDeactivate)):
            before = self.page.reading_width_preferences()
            handle, end = self.start_drag("right", -40)
            QApplication.sendEvent(target, QEvent(kind))
            self.assertFalse(handle._dragging)
            self.assertEqual(self.page.reading_width_preferences(), before)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(end))

    def test_vertical_pointer_movement_does_not_start_width_drag(self):
        handle = self.page.width_handles["left"]
        before = self.page.reading_width_preferences()
        point = handle.rect().center()
        start = handle.mapToGlobal(point)
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=point)
        end = start + QPoint(0, 35)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(handle.mapFromGlobal(end)), QPointF(end),
                            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(handle, event)
        self.assertFalse(handle._dragging)
        with patch("ui.main_window.QInputDialog.getInt", return_value=(760, False)):
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(end))
        self.assertEqual(self.page.reading_width_preferences(), before)

    def test_custom_preference_survives_narrow_window_and_returns_when_widened(self):
        w = self.window
        if w.window_state_controller.navigation_visible:
            w.toggle_navigation_panel()
        if w.window_state_controller.inspector_visible:
            w.toggle_inspector()
        self.page.set_reading_width("custom", 1120)
        self.settle()
        preferred_width = self.edit.width()
        w.resize(1100, 900)
        self.settle()
        self.assertLess(self.edit.width(), preferred_width)
        self.assertEqual(self.page.reading_width_preferences()["custom_width"], 1120)
        w.resize(1600, 900)
        self.settle()
        self.assertEqual(self.edit.width(), preferred_width)
        self.assertLessEqual(abs(self.edit.width() - 1184), 1)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_restart_restores_mode_and_custom_preference_without_manuscript(self):
        self.page.set_reading_width("custom", 1088)
        self.window.workspace_state.flush()
        data = json.loads(self.profile.read_text(encoding="utf-8"))
        self.assertEqual(data["layout"]["reading_width"], {"mode": "custom", "custom_width": 1088})
        self.assertNotIn("旧站的灯", self.profile.read_text(encoding="utf-8"))
        restored = self.make_window()
        self.assertEqual(restored.editor.writing_page.reading_width_preferences(), data["layout"]["reading_width"])
        self.assertTrue(restored.actions["width_custom"].isChecked())
        self.page.set_reading_width("fill")
        self.window.workspace_state.flush()
        filled = self.make_window()
        self.assertEqual(filled.editor.writing_page.reading_width_preferences(), {"mode": "fill", "custom_width": 1088})

    def test_dragging_beyond_window_stays_within_preference_limits(self):
        self.drag("right", 4000)
        self.assertEqual(self.page.reading_width_preferences()["custom_width"], 1600)
        self.drag("left", 4000)
        self.assertEqual(self.page.reading_width_preferences()["custom_width"], 480)
        self.assertFalse(self.window.editor.is_dirty())

    def test_real_pending_ai_candidate_stays_current_without_automatic_write(self):
        w = self.window
        candidate = "灯" * 300
        result = ExpansionRunResult(raw_output=f"<NOVEL_TEXT>{candidate}</NOVEL_TEXT>", first_raw_output=None,
            plain_text_fallback_count=0, target_chars=300, min_chars=240, max_chars=360,
            initial_char_count=300, final_char_count=300)
        token = w.ai_controller.start("expand", self.project, "chapter_01", w.editor.document_text(), lambda _cancel: result)
        self.assertIsNotNone(token)
        for _ in range(200):
            if w.ai_workflow_controller.has_pending_result and not w.ai_controller.is_running():
                break
            QTest.qWait(10)
        self.assertTrue(w.ai_workflow_controller.has_pending_result)
        for mode in ("custom", "fill", "recommended"):
            self.page.set_reading_width(mode, 900)
            self.assertTrue(w.ai_workflow_controller._task_context_matches(token))
        self.assertTrue(w.ai_workflow_controller.has_pending_result)
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertNotIn(candidate, w.editor.document_text())

    def test_legacy_boolean_preferences_restore_as_recommended_or_fill(self):
        for legacy, expected in ((True, "recommended"), (False, "fill")):
            self.profile.write_text(json.dumps({"layout": {"comfortable_width": legacy}}), encoding="utf-8")
            restored = self.make_window()
            self.assertEqual(restored.editor.writing_page.reading_width_preferences()["mode"], expected)
            self.assertTrue(restored.actions[f"width_{expected}"].isChecked())

    def test_malformed_persisted_values_are_bounded_and_safe(self):
        for value in (None, [], True, "custom", {"mode": []}, {"mode": "invalid"}):
            self.assertEqual(reading_width_preferences(value, False)["mode"], "fill")
        for value, expected in ((-1, 480), (10**10, 1600), (True, 760), ("920", 760), (None, 760), (float("nan"), 760)):
            self.assertEqual(reading_width_preferences({"mode": "custom", "custom_width": value})["custom_width"], expected)
        self.profile.write_text(json.dumps({"layout": {"reading_width": {"mode": [], "custom_width": "bad"}}}), encoding="utf-8")
        restored = self.make_window()
        self.assertEqual(restored.editor.writing_page.reading_width_preferences()["mode"], "recommended")

    def test_width_changes_do_not_trigger_auto_save_or_clear_errors_and_settings_draft(self):
        w = self.window
        w.settings_page.editor_font_size.setValue(23)
        self.assertTrue(w.settings_page.has_unsaved_changes())
        saved = QSignalSpy(w.editor.file_saved)
        for mode in ("custom", "fill", "recommended"):
            self.page.set_reading_width(mode, 832)
            self.assertFalse(w.document_controller.auto_save())
        self.assertEqual(saved.count(), 0)
        w.editor.show_save_error("磁盘只读")
        self.page.set_reading_width("custom", 900)
        self.assertEqual(w.save_status.property("saveState"), "error")
        self.assertIn("磁盘只读", w.editor.path_label.text())
        self.assertTrue(w.settings_page.has_unsaved_changes())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_handles_stay_outside_text_and_scrollbar_when_scrollbar_toggles(self):
        self.page.set_reading_width("fill")
        for source in ("短正文。", "长正文。\n" * 300, "短正文。"):
            self.edit.setPlainText(source)
            self.settle()
            viewport = self.edit.viewport()
            text_rect = QRect(viewport.mapTo(self.page, QPoint()), viewport.size())
            bar = self.edit.verticalScrollBar()
            for handle in self.page.width_handles.values():
                self.assertFalse(text_rect.intersects(handle.geometry()))
                if bar.isVisible():
                    self.assertFalse(QRect(bar.mapTo(self.page, QPoint()), bar.size()).intersects(handle.geometry()))
        self.assertEqual(self.path.read_bytes(), self.original)
