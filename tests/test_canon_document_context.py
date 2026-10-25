"""Exercise canon document commands with a real shell and disposable projects."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from tests.test_canon_schema import character_text
from ui.main_window import MainWindow


class CanonDocumentContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.project = NovelProject.create(root / "project", "资料安全测试")
        self.card = self.project.canon_dir / "characters" / "白鸥.md"
        self.card.write_text(character_text(), encoding="utf-8")
        self.world = self.project.canon_dir / "world" / "港口.md"
        self.world.write_text("# 港口\n\n## 核心规则\n\n原始规则\n", encoding="utf-8")
        self.chapter = self.project.chapters_dir / "chapter_01.md"
        restore = patch.object(MainWindow, "_restore_last_project")
        restore.start()
        self.addCleanup(restore.stop)
        self.window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False}, ui_state_path=root / "ui.json")
        self.window.project_lifecycle_controller.persist_config = lambda _: None
        self.addCleanup(self.dispose)
        self.window._load_project(self.project.root, quiet=True)
        self.window.left_panel.select_path(self.chapter)
        self.window.show()
        self.app.processEvents()
        self.window.left_panel.select_path(self.card)
        self.app.processEvents()
        self.page = self.window.structured_canon_page

    def dispose(self):
        self.window.ai_workflow_controller._pending_review = None
        self.page.reset()
        self.window.editor.clear_document()
        self.window.close()
        self.app.processEvents()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def name(self):
        return next(e for _s, k, e in self.page.form._field_widgets if k == "姓名")

    @contextmanager
    def deletion_choice(self, choice):
        with patch("ui.main_window.QMessageBox") as boxes:
            boxes.question.return_value = boxes.StandardButton.Yes
            buttons = {}
            def add(text, *_args):
                buttons[text] = object()
                return buttons[text]
            boxes.return_value.addButton.side_effect = add
            boxes.return_value.clickedButton.side_effect = lambda: buttons[choice]
            yield boxes

    def test_global_delete_and_undo_target_visible_card(self):
        self.assertEqual(self.window.current_document().path, self.card)
        self.assertEqual(self.window.window_state_controller.current_route, "canon")
        with patch.object(self.window, "_delete_chapter") as chapter_delete, patch.object(self.window, "_delete_character") as card_delete:
            self.window.actions["delete_chapter"].trigger()
            card_delete.assert_called_once_with(self.card)
            chapter_delete.assert_not_called()
        field = self.name()
        field.setFocus()
        QTest.keyClicks(field, "x")
        self.assertTrue(field.text().endswith("x"))
        with patch.object(self.window.editor, "undo") as hidden_undo:
            self.window.actions["undo"].trigger()
            self.assertFalse(field.text().endswith("x"))
            hidden_undo.assert_not_called()
        self.window.actions["redo"].trigger()
        self.assertTrue(field.text().endswith("x"))
        self.assertTrue(self.window.actions["find"].isEnabled())
        self.assertFalse(self.window.actions["expand"].isEnabled())
        self.assertFalse(self.window.actions["style_review"].isEnabled())
        self.window.actions["focus"].trigger()
        self.assertIs(self.app.focusWidget(), field)

    def test_locate_and_quick_open_keep_canon_document(self):
        self.window.toggle_focus_mode()
        self.window._locate_current_document()
        self.assertFalse(self.window.window_state_controller.focus_mode)
        self.assertTrue(self.window.left_panel.isVisible())
        self.assertIs(self.window.content_stack.currentWidget(), self.page)
        selected = self.window.left_panel.tree.currentItem()
        self.assertEqual(selected.data(0, self.window.left_panel.PATH_ROLE), str(self.card))
        controller = self.window.quick_access_controller
        entry = next(e for e in controller.entries() if e.key == str(self.world))
        self.assertTrue(controller.activate(entry))
        self.assertEqual(self.window.current_document().path, self.world)
        command = next(e for e in controller.entries() if e.key == "save")
        self.assertEqual(controller.unavailable(command), "")
        self.window.left_panel.select_path(self.chapter)
        self.assertFalse(self.window.current_document().structured)
        self.assertEqual(self.window.current_document().path, self.chapter)
        self.assertEqual(self.window.window_state_controller.current_route, "writing")

    def test_cancel_switch_restores_tree_selection(self):
        self.name().setText("未保存")
        with patch.object(self.page, "confirm_discard_or_save", return_value=False):
            self.window.left_panel.select_path(self.world)
        self.assertEqual(self.window.current_document().path, self.card)
        item = self.window.left_panel.tree.currentItem()
        self.assertEqual(item.data(0, self.window.left_panel.PATH_ROLE), str(self.card))
        self.assertEqual(self.name().text(), "未保存")

    def test_project_change_refreshes_clean_card_and_preserves_dirty_conflict(self):
        changed = character_text().replace("- 姓名：白鸥", "- 姓名：同步后")
        self.card.write_text(changed, encoding="utf-8")
        self.window.project_session.notify_data_changed([self.card], kind="canon")
        self.assertEqual(self.name().text(), "同步后")
        self.name().setText("本地草稿")
        external = changed + "\n外部更新\n"
        self.card.write_text(external, encoding="utf-8")
        self.window.project_session.notify_data_changed([self.card], kind="canon")
        self.assertEqual(self.name().text(), "本地草稿")
        self.assertEqual(self.page.modified_badge.text(), "外部版本变化")
        self.assertTrue(self.page.diff_button.isVisible())
        copy = Path(self.tmp.name) / "draft.md"
        self.assertFalse(self.page.export_draft(self.card)[0])
        self.assertTrue(self.page.export_draft(copy)[0])
        self.assertIn("本地草稿", copy.read_text(encoding="utf-8"))
        self.assertEqual(self.card.read_text(encoding="utf-8"), external)
        self.assertTrue(self.page.is_modified())
        with patch("ui.structured_canon_page.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(self.page.reload_from_disk())
        self.assertEqual(self.name().text(), "本地草稿")
        with patch("ui.structured_canon_page.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.page.reload_from_disk())
        self.assertEqual(self.name().text(), "同步后")
        self.assertFalse(self.page.is_modified())

    def test_save_then_delete_stores_latest_card_in_trash(self):
        self.name().setText("回收站保留最新草稿")
        with self.deletion_choice("保存后删除"):
            self.window.delete_current_chapter()
        self.assertFalse(self.card.exists())
        self.assertTrue(self.chapter.exists())
        copies = list(self.project.root.glob("**/character.md"))
        self.assertTrue(any("回收站保留最新草稿" in p.read_text(encoding="utf-8") for p in copies))
        self.assertIsNone(self.page.current_path())

    def test_cancel_delete_keeps_draft(self):
        self.name().setText("未保存草稿")
        with self.deletion_choice("取消"):
            self.window.delete_current_chapter()
        self.assertTrue(self.card.exists())
        self.assertEqual(self.name().text(), "未保存草稿")
        self.assertTrue(self.page.is_modified())

    def test_failed_save_or_delete_keeps_draft(self):
        self.name().setText("未保存草稿")
        with self.deletion_choice("保存后删除"), patch("ui.canon_form_widget.atomic_write_text", side_effect=OSError("disk full")), patch.object(self.window.document_controller, "delete_character") as delete:
            self.window.delete_current_chapter()
            delete.assert_not_called()
        self.assertTrue(self.card.exists())
        self.assertEqual(self.name().text(), "未保存草稿")
        with self.deletion_choice("放弃修改并删除"), patch.object(self.window.document_controller, "delete_character", side_effect=OSError("permission denied")):
            self.window.delete_current_chapter()
        self.assertTrue(self.page.is_modified())
        self.assertEqual(self.name().text(), "未保存草稿")

    def test_world_delete_and_pending_ai_review_protection(self):
        self.window.left_panel.select_path(self.world)
        editor = self.page.form._text_widgets[0][1]
        editor.setPlainText("最新世界观")
        with self.deletion_choice("保存后删除"):
            self.window.delete_current_chapter()
        self.assertFalse(self.world.exists())
        copies = list(self.project.root.glob("**/document.md"))
        self.assertTrue(any("最新世界观" in p.read_text(encoding="utf-8") for p in copies))
        self.window.left_panel.select_path(self.card)
        self.window.ai_workflow_controller._pending_review = (object(), object())
        self.window._refresh_delete_action()
        self.assertFalse(self.window.actions["delete_chapter"].isEnabled())
        with patch("ui.main_window.QMessageBox.information"), patch.object(self.window.document_controller, "delete_character") as delete:
            self.window.delete_character_by_path(str(self.card))
            delete.assert_not_called()
        self.assertTrue(self.card.exists())

    def test_hidden_editor_is_saved_by_its_owner(self):
        self.window.editor.text_edit.insertPlainText("hidden chapter draft")
        with patch.object(self.page, "confirm_discard_or_save", return_value=True):
            self.assertTrue(self.window._save_if_dirty())
        self.assertIn("hidden chapter draft", self.chapter.read_text(encoding="utf-8"))
        self.assertFalse(self.window.editor.is_dirty())
        self.assertNotIn("hidden chapter draft", self.card.read_text(encoding="utf-8"))

    def test_hidden_editor_also_saves_while_virtual_timeline_is_visible(self):
        self.window.editor.text_edit.insertPlainText("timeline background draft")
        self.window.content_stack.setCurrentWidget(self.window.timeline_page)
        self.assertIsNone(self.window.current_document().path)
        self.assertTrue(self.window._save_if_dirty())
        self.assertIn("timeline background draft", self.chapter.read_text(encoding="utf-8"))
        self.assertFalse(self.window.editor.is_dirty())

    def test_undo_after_removing_its_field_does_not_edit_another_field(self):
        field = self.name()
        field.setFocus()
        QTest.keyClicks(field, "x")
        relation = self.page.form._relation_rows[0][1]
        relation.setFocus()
        self.app.processEvents()
        self.page.form._remove_relation_row(relation, relation.parentWidget())
        self.window.actions["undo"].trigger()
        self.assertTrue(self.name().text().endswith("x"))
        self.assertEqual(len(self.page.form._relation_rows), 1)


if __name__ == "__main__":
    unittest.main()
