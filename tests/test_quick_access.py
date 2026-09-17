import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from ui.main_window import MainWindow
from ui.quick_access import QuickAccessDialog, QuickEntry, match_entries


class QuickMatchingTests(unittest.TestCase):
    def test_tokens_title_priority_and_literal_search(self):
        entries = [QuickEntry("a", "港口", "章节 · fog.md"), QuickEntry("b", "Fog 来信", "角色 · letter.md"), QuickEntry("c", "[来信]", "世界观")]
        self.assertEqual([e.key for e in match_entries(entries, "FOG")], ["b", "a"])
        self.assertEqual([e.key for e in match_entries(entries, "fog 角色")], ["b"])
        self.assertEqual([e.key for e in match_entries(entries, "[")], ["c"])
        self.assertEqual(match_entries(entries, "   "), entries)


class QuickAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = QCoreApplication.instance()
        if app is not None and not isinstance(app, QApplication):
            raise unittest.SkipTest("Requires QApplication")
        cls.app = app or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.project = NovelProject.create(root / "novel", "快速导航测试")
        self.chapter = self.project.chapters_dir / "chapter_01.md"
        self.chapter.write_text("# 雾港来信\n\n## 正文\n故事开头。", encoding="utf-8")
        self.second = self.project.chapters_dir / "chapter_02.md"
        self.second.write_text("# 同名资料\n\n## 正文\n另一章节。", encoding="utf-8")
        (self.project.canon_dir / "world" / "harbor.md").write_text("# 同名资料\n\n港口设定。", encoding="utf-8")
        patcher = patch.object(MainWindow, "_restore_last_project")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False})
        self.window.project_lifecycle_controller.persist_config = lambda _config: None
        self.addCleanup(self.window.close)
        self.window.project_session.set_project(self.project)
        self.window.left_panel.select_path(self.chapter)
        self.window.resize(1100, 720)
        self.window.show()
        self.events()
        self.controller = self.window.quick_access_controller

    def events(self):
        for _ in range(3):
            self.app.processEvents()

    def entry(self, key):
        return next(e for e in self.controller.entries() if e.key == str(key))

    def dialog(self, entries=None, reason=None, mode="documents"):
        dialog = QuickAccessDialog(entries if entries is not None else self.controller.entries(), reason or self.controller.unavailable, mode=mode, parent=self.window)
        self.addCleanup(dialog.close)
        dialog.show()
        self.events()
        return dialog

    def test_keyboard_selects_without_opening_until_enter(self):
        entries = [QuickEntry("a", "第一项", "章节"), QuickEntry("b", "第二项", "资料")]
        dialog = self.dialog(entries, lambda _entry: "")
        dialog.search.setFocus()
        QTest.keyClick(dialog.search, Qt.Key.Key_Down)
        self.assertEqual(dialog.results.currentRow(), 1)
        self.assertIsNone(dialog.selected_entry)
        QTest.keyClick(dialog.search, Qt.Key.Key_Return)
        self.assertEqual(dialog.selected_entry.key, "b")
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_disabled_command_rechecks_live_availability(self):
        dialog = self.dialog(mode="commands")
        dialog.search.setText("AI 扩写")
        self.window.actions["expand"].setEnabled(False)
        self.window.actions["expand"].setStatusTip("请先审阅结果")
        QTest.keyClick(dialog.search, Qt.Key.Key_Return)
        self.assertIsNone(dialog.selected_entry)
        self.assertIn("请先审阅结果", dialog.detail.text())
        with patch.object(self.window.actions["expand"], "trigger") as trigger:
            self.assertFalse(self.controller.activate(self.entry("expand")))
            trigger.assert_not_called()

    def test_no_matches_and_large_inventory_are_bounded(self):
        dialog = self.dialog([QuickEntry(str(i), f"章节 {i}", f"目录/{i}.md") for i in range(5000)], lambda _entry: "")
        self.assertEqual(dialog.results.count(), dialog.MAX_VISIBLE)
        self.assertIn("5000", dialog.count_label.text())
        dialog.search.setText("不存在")
        QTest.keyClick(dialog.search, Qt.Key.Key_Return)
        self.assertIsNone(dialog.selected_entry)
        self.assertIn("没有匹配", dialog.detail.text())
        dialog.search.setText("目录/4999.md")
        self.assertEqual(dialog.results.count(), 1)

    def test_search_includes_hidden_canon_and_preserves_filter_until_activation(self):
        self.window.left_panel.search.setText("没有匹配")
        entries = self.controller.entries()
        self.assertTrue(any(e.category == "世界观" for e in entries))
        self.assertEqual(self.window.left_panel.search.text(), "没有匹配")
        self.assertTrue(self.controller.activate(self.entry(self.second)))
        self.assertEqual(self.window.editor.current_path(), str(self.second))
        self.assertEqual(self.window.left_panel.search.text(), "")
        self.assertEqual(self.controller.entries()[0].key, str(self.second))

    def test_failed_save_prevents_quick_open(self):
        self.window.editor.text_edit.insertPlainText("未保存文字")
        entry = self.entry(self.second)
        with patch.object(self.window.document_controller.document_service, "save_document", side_effect=OSError("disk full")):
            self.assertFalse(self.controller.activate(entry))
        self.assertEqual(self.window.editor.current_path(), str(self.chapter))
        self.assertIn("未保存文字", self.window.editor.document_text())
        self.assertTrue(self.window.document_controller.save())

    def test_stale_deleted_and_outside_paths_cannot_open(self):
        entry = self.entry(self.second)
        self.second.unlink()
        self.assertTrue(self.controller.unavailable(entry))
        outside = QuickEntry(str(Path(self.tmp.name) / "secret.md"), "secret", "", project_root=str(self.project.root))
        self.assertTrue(self.controller.unavailable(outside))
        other = NovelProject.create(Path(self.tmp.name) / "other", "另一个项目")
        self.window.project_session.set_project(other)
        self.assertIn("项目已切换", self.controller.unavailable(entry))

    def test_command_uses_existing_action_and_shortcut(self):
        entry = self.entry("save")
        self.assertEqual(entry.shortcut, "Ctrl+S")
        ai_keys = {item.key for item in match_entries(self.controller.entries(), "AI")}
        self.assertTrue({"expand", "continuation", "check", "memory"}.issubset(ai_keys))
        with patch.object(self.window.actions["save"], "trigger") as trigger:
            self.assertTrue(self.controller.activate(entry))
            trigger.assert_called_once()

    def test_modal_escape_does_not_exit_focus_mode(self):
        self.window.window_state_controller.toggle_focus_mode()
        seen = []
        def close_dialog():
            dialog = self.app.activeModalWidget()
            seen.append(isinstance(dialog, QuickAccessDialog))
            QTest.keyClick(dialog.search, Qt.Key.Key_Escape)
        QTimer.singleShot(50, close_dialog)
        self.window.open_quick_access()
        self.assertEqual(seen, [True])
        self.assertTrue(self.window.window_state_controller.focus_mode)

    def test_shortcut_opens_palette_and_can_switch_modes(self):
        seen = []
        def inspect_dialog():
            dialog = self.app.activeModalWidget()
            if isinstance(dialog, QuickAccessDialog):
                seen.append(dialog.mode)
                QTest.keyClick(dialog.search, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
                seen.append(dialog.mode)
                dialog.reject()
        self.window.window_state_controller.toggle_focus_mode()
        self.window.activateWindow()
        self.window.editor.text_edit.setFocus()
        self.events()
        QTimer.singleShot(50, inspect_dialog)
        QTest.keyClick(self.window.editor.text_edit, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(seen, ["documents", "commands"])

    def test_no_project_documents_explain_how_to_start(self):
        self.window.project_session.set_project(None)
        dialog = self.dialog()
        self.assertEqual(dialog.results.count(), 0)
        self.assertIn("尚未打开项目", dialog.detail.text())
        dialog.set_mode("commands")
        dialog.search.setText("打开项目")
        self.assertGreater(dialog.results.count(), 0)

    def test_open_current_document_from_dashboard_returns_to_editor(self):
        self.window._show_route("dashboard")
        self.assertTrue(self.controller.activate(self.entry(self.chapter)))
        self.assertEqual(self.window.window_state_controller.current_route, "writing")
        self.assertEqual(self.window.editor.current_path(), str(self.chapter))

    def test_focus_directory_reopens_sidebar_and_selects_query(self):
        self.window.window_state_controller.toggle_navigation_panel()
        self.window.left_panel.search.setText("来信")
        self.window.focus_directory()
        self.events()
        self.assertTrue(self.window.left_panel.isVisible())
        self.assertEqual(self.window.left_panel.search.selectedText(), "来信")
