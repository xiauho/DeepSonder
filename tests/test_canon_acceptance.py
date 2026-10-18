"""Offline acceptance journeys through the real shell using disposable projects."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPlainTextEdit
from core.canon_schema import parse_canon_entry
from core.character_card_sync import (MANAGED_STATE_START, MANAGED_STATE_END,
    CharacterCardSyncError, build_character_card_sync_proposal, apply_character_card_sync,
    parse_managed_state)
from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from core.task_context import AIContextSnapshot
from core.task_controller import AITaskToken
from tests.test_canon_schema import character_text
from tests import test_character_card_sync as sync_fixture
from ui.canon_form_widget import NewModuleDialog
from ui.main_window import MainWindow


class CanonAcceptanceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = NovelProject.create(self.root / "project", "模块资料验收")
        self.card = self.project.canon_dir / "characters" / "白鸥.md"
        source = "\ufeff" + character_text().replace("# 白鸥\n\n", "# 白鸥\n\n港口守灯人的资料导语。\n\n")
        source += "\n## 原始对照表\n\n| 标记 | 含义 |\n| --- | --- |\n| A | 保留原文 |\n"
        self.card.write_bytes(source.replace("\n", "\r\n").encode("utf-8"))
        self.world = self.project.canon_dir / "world" / "港口.md"
        self.world.write_bytes("# 港口\n\n## 核心规则\n\n原始规则\n".encode("utf-8"))
        restore = patch.object(MainWindow, "_restore_last_project")
        restore.start(); self.addCleanup(restore.stop)
        for method in ("information", "warning"):
            popup = patch("ui.main_window.QMessageBox." + method)
            setattr(self, method, popup.start())
            self.addCleanup(popup.stop)
        self.window = None
        self.open_window()
        self.addCleanup(self.close_window)

    @property
    def page(self):
        return self.window.structured_canon_page

    @property
    def form(self):
        return self.page.form

    def open_window(self):
        self.window = MainWindow(config={**DEFAULT_CONFIG, "auto_save":False}, ui_state_path=self.root / "ui.json")
        self.window.project_lifecycle_controller.persist_config = lambda _: None
        self.window._load_project(self.project.root, quiet=True)
        self.window.left_panel.select_path(self.project.chapters_dir / "chapter_01.md")
        self.window.show(); self.app.processEvents()
        self.window.left_panel.select_path(self.card); self.app.processEvents()

    def close_window(self):
        if self.window is None:
            return
        self.window.ai_workflow_controller.discard_pending_result()
        self.page.reset(); self.window.editor.clear_document()
        self.window.close(); self.window.deleteLater()
        self.window = None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.window = None

    def field(self, key):
        return next(editor for section, name, editor in self.form._field_widgets if name == key)

    def prompt(self, callback, text):
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=(text, True)):
            callback()

    def module(self, name, role):
        dialog = NewModuleDialog(self.form)
        dialog.name_edit.setText(name); dialog.role_buttons[role].setChecked(True)
        dialog.confirm()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog.deleteLater()
        return self.form.entry.section(name)

    def nav_item(self, path):
        panel = self.window.left_panel
        return next(item for item in panel.document_items() if item.data(0,panel.PATH_ROLE) == str(path))

    def test_edit_structure_collapse_global_save_navigation_and_restart(self):
        before = self.card.read_bytes()
        untouched_tail = before[before.index("## 作者自由备注".encode("utf-8")):]
        chapter = self.project.chapters_dir / "chapter_01.md"
        chapter_before = chapter.read_bytes()
        world_before = self.world.read_bytes()
        self.field("姓名").clear()
        custom = self.module("习惯", "fields")
        self.prompt(lambda: self.form._add_field(custom), "偏好")
        self.field("偏好").setText("温茶与潮声")
        self.prompt(lambda: self.form._rename_field(self.form.entry.section("习惯"), "偏好"), "喜好")
        grouped = self.module("物品", "group")
        self.prompt(lambda: self.form._add_group(grouped), "钥匙")
        self.prompt(lambda: self.form._add_field(self.form.entry.section("物品"), "钥匙"), "来源")
        editor = next(e for s,g,k,e in self.form._group_widgets if g == "钥匙" and k == "来源")
        editor.setText("旧站")
        self.prompt(lambda: self.form._rename_group(self.form.entry.section("物品"), "钥匙"), "铜钥匙")
        self.form.set_modules_expanded(False)
        self.assertEqual(self.card.read_bytes(), before)
        self.window.actions["save"].trigger()
        self.assertFalse(self.page.is_modified())
        self.assertTrue(self.card.read_bytes().endswith(untouched_tail))
        self.assertNotIn(b"\n", self.card.read_bytes().replace(b"\r\n", b""))
        self.assertEqual(self.nav_item(self.card).data(0,self.window.left_panel.CANON_STATUS_ROLE)["missing_fields"], 1)
        self.field("姓名").setText("白鸥")
        self.window.actions["save"].trigger()
        saved = self.card.read_bytes()
        entries = self.window.quick_access_controller.entries()
        self.assertTrue(self.window.quick_access_controller.activate(next(e for e in entries if e.key == str(self.world))))
        self.window.left_panel.select_path(self.card)
        self.assertEqual(self.form.entry.section("习惯").module_id, custom.module_id)
        self.assertEqual(self.field("喜好").text(), "温茶与潮声")
        self.assertFalse(self.form._module_toggles[custom.module_id].isChecked())
        self.close_window(); self.open_window()
        self.assertEqual(self.field("喜好").text(), "温茶与潮声")
        self.assertEqual(self.form.entry.section("物品").groups[0].name, "铜钥匙")
        self.assertTrue(self.form._module_toggles[custom.module_id].isChecked())
        self.window.actions["save"].trigger()
        self.assertEqual(self.card.read_bytes(), saved)
        self.assertEqual(chapter.read_bytes(), chapter_before)
        self.assertEqual(self.world.read_bytes(), world_before)

    def test_external_conflict_diff_copy_cancel_reload_and_resume(self):
        self.field("姓名").setText("本地草稿")
        self.module("待确认", "fields")
        external = self.card.read_bytes() + "\r\n外部作者补充。\r\n".encode("utf-8")
        self.card.write_bytes(external)
        self.window.project_session.notify_data_changed([self.card], kind="canon")
        self.assertTrue(self.form.external_changed)
        self.assertFalse(self.page.write_changes()[0])
        self.assertEqual(self.card.read_bytes(), external)
        captured = []
        def inspect(dialog):
            captured.append(dialog.findChild(QPlainTextEdit).toPlainText())
            return QDialog.DialogCode.Accepted
        with patch("ui.structured_canon_page.QDialog.exec", new=inspect):
            self.page.show_revision_diff()
        self.assertIn("外部作者补充", captured[0])
        self.assertNotIn("-\ufeff# 白鸥", captured[0])
        copy = self.root / "草稿.md"
        self.assertTrue(self.page.export_draft(copy)[0])
        self.assertIn("待确认", copy.read_text(encoding="utf-8"))
        self.assertIn("本地草稿", copy.read_text(encoding="utf-8"))
        with patch("ui.structured_canon_page.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(self.page.reload_from_disk())
        self.assertEqual(self.field("姓名").text(), "本地草稿")
        with patch("ui.structured_canon_page.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.page.reload_from_disk())
        self.assertEqual(self.field("姓名").text(), "白鸥")
        self.assertIsNone(self.form.entry.section("待确认"))
        self.field("姓名").setText("恢复后编辑")
        self.window.actions["save"].trigger()
        self.assertIn("外部作者补充", self.card.read_bytes().decode("utf-8"))
        self.assertFalse(self.page.is_modified())

    def test_sync_review_cancel_confirm_refresh_and_stale_proposal(self):
        project, card = sync_fixture.CharacterCardSyncTests()._project_with_memories(self.root / "sync")
        self.project, self.card = project, card
        card.write_bytes(card.read_bytes().replace(b"\n", b"\r\n"))
        self.window._load_project(project.root, quiet=True)
        self.window.left_panel.select_path(card)
        self.prompt(lambda: self.form._rename_module(self.form.entry.section("能力档案")), "成长路线")
        self.window.actions["save"].trigger()
        original = card.read_bytes()
        with patch("ui.main_window.CharacterCardSyncSelectionDialog") as select, patch("ui.main_window.CharacterCardSyncPreviewDialog") as preview:
            select.return_value.exec.return_value = QDialog.DialogCode.Accepted
            select.return_value.selection.return_value = (card, ("chapter_01", "chapter_02"))
            preview.return_value.exec.return_value = QDialog.DialogCode.Rejected
            self.window.sync_character_card(str(card))
            self.assertEqual(card.read_bytes(), original)
            preview.return_value.exec.return_value = QDialog.DialogCode.Accepted
            preview.return_value.selected_fields.return_value = ("location", "power_level", "items")
            self.window.sync_character_card(str(card))
        updated = card.read_bytes()
        start, end = MANAGED_STATE_START.encode(), MANAGED_STATE_END.encode()
        self.assertEqual(original[:original.index(start)], updated[:updated.index(start)])
        self.assertEqual(original[original.index(end)+len(end):], updated[updated.index(end)+len(end):])
        self.assertNotIn(b"\n", updated.replace(b"\r\n", b""))
        self.assertEqual(parse_managed_state(updated.decode("utf-8"))["location"], "北塔")
        managed = next(s for s in self.form.entry.sections if s.role == "managed")
        self.assertIn("北塔", self.form._module_bodies[managed.module_id].findChild(QPlainTextEdit).toPlainText())
        self.assertTrue(self.form._module_bodies[managed.module_id].findChild(QPlainTextEdit).isReadOnly())
        self.assertTrue(self.nav_item(card).data(0,self.window.left_panel.CANON_STATUS_ROLE)["synced"])
        stale = build_character_card_sync_proposal(project, card, ("chapter_01", "chapter_02"))
        self.field("别名").setText("资料编辑后变更")
        self.window.actions["save"].trigger()
        latest = card.read_bytes()
        with self.assertRaisesRegex(CharacterCardSyncError, "已发生变化"):
            apply_character_card_sync(project, stale, ("state",))
        self.assertEqual(card.read_bytes(), latest)

    def test_pending_ai_result_stales_after_saved_canon_edit_and_blocks_delete(self):
        controller = self.window.ai_controller
        token = AITaskToken("check", "chapter_01")
        source = self.window.editor.text_edit.toPlainText()
        snapshot = AIContextSnapshot.capture(self.project, "chapter_01", source, task_kind="check")
        controller._result_snapshots[token.task_id] = snapshot
        controller._on_task_succeeded(token, json.dumps({"type":"consistency_report", "chapter_id":"chapter_01", "issues":[]}))
        controller._on_task_finished(token)
        self.assertTrue(self.window.ai_workflow_controller.has_pending_result)
        chapter = self.project.chapters_dir / "chapter_01.md"
        before = chapter.read_bytes()
        self.assertFalse(self.window.actions["delete_chapter"].isEnabled())
        with patch("ui.main_window.QMessageBox.information"), patch.object(self.window.document_controller, "delete_character") as delete:
            self.window.delete_character_by_path(str(self.card))
            delete.assert_not_called()
        self.field("姓名").setText("审阅前新资料")
        self.window.actions["save"].trigger()
        self.assertFalse(controller.context_matches(self.project, "chapter_01", source, token))
        with patch("ui.ai_workflow_controller.QMessageBox.warning") as warning, patch.object(self.window.ai_workflow_controller.ai_result_service,"parse_consistency") as parse:
            self.window.ai_workflow_controller.review_pending_result()
            warning.assert_called_once()
            parse.assert_not_called()
        self.assertFalse(self.window.ai_workflow_controller.has_pending_result)
        self.assertEqual(chapter.read_bytes(), before)
        self.assertEqual(self.window.current_document().path, self.card)
        self.assertNotIn(token.task_id, controller._result_snapshots)


    def test_queued_navigation_and_startup_callbacks_end_with_window(self):
        for _ in range(2):
            self.window.left_panel.select_path(self.project.chapters_dir / "chapter_01.md")
            self.window.window_state_controller.toggle_navigation_panel()
            self.window.window_state_controller.toggle_navigation_panel()
            self.close_window()
            QTest.qWait(100)
            self.open_window()
            self.assertEqual(self.window.current_document().path, self.card)
        self.assertFalse(self.page.is_modified())


if __name__ == "__main__":
    import unittest
    unittest.main()
