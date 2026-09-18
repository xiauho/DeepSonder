from tests.style_fixtures import set_style
from core.style_library import load_library
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from shiboken6 import isValid

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
        self.addCleanup(self.dispose_window)
        self.window.project_session.set_project(self.project)
        self.window.left_panel.select_path(self.chapter)
        self.window.resize(1100, 720)
        self.window.show()
        self.events()
        self.controller = self.window.quick_access_controller

    def dispose_window(self):
        # close() only hides QMainWindow. Without deferred destruction, every
        # test retains another widget tree and subsequent global style updates
        # become progressively slower until the CI module timeout is reached.
        # Drain queued layout/scroll restoration while their widgets are alive.
        self.events()
        self.window.close()
        self.events()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertFalse(isValid(self.window), "Test window survived cleanup")

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

    def test_old_style_document_is_not_restored_as_startup_modal(self):
        root = str(self.project.root.resolve())
        self.window.workspace_state.data["projects"][root] = {"last": "writing/style_guide.md"}
        self.assertIsNone(self.window.workspace_state.last_document(self.project))

    def test_footer_opens_same_manager_and_flat_documents_are_searchable(self):
        self.window.left_panel.set_scope("canon")
        with patch.object(self.window.ai_workflow_controller, "manage_style_library", return_value=True) as manage:
            self.window.left_panel.style_button.click()
            manage.assert_called_once()
        for path in (self.project.outline_dir / "story_plan.json",):
            self.assertTrue(self.controller.activate(self.entry(path)))
            self.assertEqual(self.window.editor.current_path(), str(path))
            self.assertTrue(self.window.left_panel.locate_button.isEnabled())

    def test_timeline_feature_is_searchable_without_a_file(self):
        (self.project.canon_dir / "timeline.md").unlink()
        self.window.left_panel.set_project(self.project)
        entry = self.entry("timeline")
        self.assertEqual(entry.feature, "timeline")
        self.assertTrue(self.controller.activate(entry))
        self.assertIs(self.window.content_stack.currentWidget(), self.window.timeline_page)
        self.assertIsNone(self.window.left_panel.tree.currentItem().data(0, self.window.left_panel.PATH_ROLE))

    def test_style_tree_and_quick_open_share_manager(self):
        current = self.window.editor.current_path()
        entry = self.entry("style_library")
        self.assertEqual(entry.title, "本书文风…")
        with patch.object(self.window.ai_workflow_controller, "manage_style_library", return_value=True) as manage:
            self.window._on_file_selected("本书文风", str(self.project.style_guide_path))
            self.assertTrue(self.controller.activate(entry))
            self.assertEqual(manage.call_count, 2)
        self.assertEqual(self.window.editor.current_path(), current)

    def test_style_manager_preserves_draft_when_save_fails(self):
        controller = self.window.ai_workflow_controller
        with patch.object(controller, "save_if_dirty", return_value=False), patch("ui.style_library_dialog.StyleLibraryDialog") as dialog:
            self.assertFalse(controller.manage_style_library())
            dialog.assert_not_called()

    def test_style_manager_conflict_keeps_draft_and_external_content(self):
        from ui.style_library_dialog import StyleLibraryDialog
        calls = []
        def submit(dialog):
            calls.append(True)
            if len(calls) == 1:
                dialog.fields["总体气质"].setPlainText("待保存的要求")
                set_style(self.project.root, "外部修改")
                dialog._submit()
                return QDialog.DialogCode.Accepted
            self.assertEqual(dialog.fields["总体气质"].toPlainText(), "待保存的要求")
            self.assertIn("其他窗口修改", dialog.error.text())
            return QDialog.DialogCode.Rejected
        with patch.object(StyleLibraryDialog, "exec", submit):
            self.window.ai_workflow_controller.manage_style_library()
        self.assertEqual(load_library(self.project.root)["profile"]["总体气质"], "外部修改")
        self.assertEqual(len(calls), 2)

    def test_style_manager_cancel_leaves_all_style_files_unchanged(self):
        from application.book_style_service import snapshot
        from ui.style_library_dialog import StyleLibraryDialog
        before = snapshot(self.project.root)
        def cancel(dialog):
            dialog.fields["总体气质"].setPlainText("不应保存")
            dialog.enabled.setChecked(True)
            return QDialog.DialogCode.Rejected
        with patch.object(StyleLibraryDialog, "exec", cancel):
            self.window.ai_workflow_controller.manage_style_library()
        self.assertEqual(snapshot(self.project.root), before)

    def test_style_manager_confirmation_persists_requirements(self):
        from ui.style_library_dialog import StyleLibraryDialog
        def accept(dialog):
            dialog.fields["总体气质"].setPlainText("统一管理的本书要求")
            dialog._submit()
            return QDialog.DialogCode.Accepted
        with patch.object(StyleLibraryDialog, "exec", accept):
            self.assertTrue(self.window.ai_workflow_controller.manage_style_library())
        self.assertEqual(load_library(self.project.root)["profile"]["总体气质"], "统一管理的本书要求")
        self.assertEqual(self.window.editor.current_path(), str(self.chapter))

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
