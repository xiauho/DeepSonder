import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import (QApplication, QDialog, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea)
from PySide6.QtGui import QFontDatabase
from core.canon_schema import SECTION_START, load_canon_entry
from core.character_card_sync import MANAGED_STATE_START
from core.project_data import CANON_ENTRY_TYPES
from tests.test_canon_index import FakeProject
from tests.test_canon_schema import character_text
from ui.canon_form_widget import CanonFormWidget, MODE_SOURCE, NewModuleDialog
from ui.structured_canon_page import StructuredCanonPage
from ui.theme import apply_theme


class CanonFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc") if Path("C:/Windows/Fonts/msyh.ttc").exists() else -1

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.canon = Path(self.tmp.name) / "canon" / "characters"
        self.canon.mkdir(parents=True)
        self.path = self.canon / "白鸥.md"
        self.path.write_text(character_text(), encoding="utf-8")
        self.addCleanup(lambda: apply_theme(self.app, {'theme': 'light'}))

    def widget(self, **kwargs):
        widget = CanonFormWidget()
        widget.load_entry(self.path, "character", **kwargs)
        return widget

    def page(self, project=None, path=None, kind="character"):
        page = StructuredCanonPage()
        page.open_path(path or self.path, kind, project)
        return page

    def field(self, host, key):
        widget = host.form if isinstance(host, StructuredCanonPage) else host
        return next(e for section, k, e in widget._field_widgets if k == key)

    def group_field(self, host, group_name, key):
        widget = host.form if isinstance(host, StructuredCanonPage) else host
        return next(e for s, g, k, e in widget._group_widgets if g == group_name and k == key)

    def fake_project(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        project = FakeProject(Path(tmp.name))
        self.baiou = project.canon_dir / "characters" / "白鸥.md"
        self.baiou.write_text(character_text(), encoding="utf-8")
        self.linzhou = project.canon_dir / "characters" / "林舟.md"
        self.linzhou.write_text("# 林舟\n\n## 基础档案\n\n- 姓名：林舟\n", encoding="utf-8")
        self.beigang = project.canon_dir / "world" / "北港.md"
        self.beigang.write_text(
            str(CANON_ENTRY_TYPES["world"]["template"]).format(title="北港"), encoding="utf-8")
        return project

    def _patch_confirm_prompt(self, choice):
        """把表单的未保存三选一替换为可控桩，返回 {按钮文本: 按钮}。"""
        patcher = patch("ui.canon_form_widget.QMessageBox")
        message_box = patcher.start()
        self.addCleanup(patcher.stop)
        buttons = {}

        def fake_add(text, *args, **kwargs):
            button = MagicMock()
            buttons[text] = button
            return button

        box = message_box.return_value
        box.addButton.side_effect = fake_add
        box.clickedButton.side_effect = lambda: buttons[choice]
        return buttons

    # ---------- 保存语义（字节保留 / 无变化零写 / 修订锁 / 源码直写） ----------

    def test_form_edit_marks_section_and_preserves_rest_bytes(self):
        widget = self.widget()
        self.field(widget, "身份／阵营").setText("北港向导")
        ok, error = widget.write_changes()
        self.assertTrue(ok)
        self.assertEqual(error, "")
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn(SECTION_START, saved)
        self.assertIn("- 身份／阵营：北港向导", saved)
        self.assertIn("- 别名：林舟", saved)
        self.assertIn("临时想法。", saved)
        self.assertIn(MANAGED_STATE_START, saved)
        reparsed = load_canon_entry(self.path, "character")
        self.assertEqual(reparsed.section("当前剧情状态（DeepSonder 同步）").role, "managed")
        self.assertFalse(widget.is_modified())

    def test_save_without_change_writes_nothing(self):
        before = self.path.read_bytes()
        widget = self.widget()
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        self.assertEqual(self.path.read_bytes(), before)

    def test_revision_lock_rejects_external_modification(self):
        widget = self.widget()
        self.field(widget, "姓名").setText("白鸥改名")
        external = character_text().replace("谨慎。", "外部修改后的性格。")
        self.path.write_text(external, encoding="utf-8")
        ok, error = widget.write_changes()
        self.assertFalse(ok)
        self.assertIn("重新打开", error)
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)
        self.assertTrue(widget.is_modified())

    def test_deleted_file_save_reports_error_without_crash(self):
        widget = self.widget()
        self.field(widget, "姓名").setText("白鸥改名")
        self.path.unlink()
        ok, error = widget.write_changes()
        self.assertFalse(ok)
        self.assertIn("重新打开", error)

    def test_source_mode_edit_writes_through_and_form_reparses(self):
        widget = self.widget()
        widget._mode_buttons[MODE_SOURCE].click()
        self.assertEqual(widget.stack.currentIndex(), 1)
        widget.source_edit.setPlainText(widget.source_edit.toPlainText() + "\n## 附记\n\n手写追加。\n")
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("## 附记", saved)
        reparsed = load_canon_entry(self.path, "character")
        self.assertEqual(reparsed.section("附记").role, "text")

    def test_managed_section_is_readonly_without_field_widgets(self):
        widget = self.widget()
        managed = next(s for s in widget.entry.sections if s.role == "managed")
        self.assertFalse([w for s, k, w in widget._field_widgets if s is managed])
        box = next(b for b in widget.form_scroll.findChildren(QPlainTextEdit)
                   if managed.heading and b.toPlainText().startswith(MANAGED_STATE_START.strip()))
        self.assertTrue(box.isReadOnly())

    def test_group_field_edit_persists(self):
        widget = self.widget()
        self.group_field(widget, "潮汐感知", "当前等级").setText("二阶")
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("- 当前等级：二阶", saved)
        reparsed = load_canon_entry(self.path, "character")
        groups = reparsed.section("能力档案").groups
        self.assertEqual([g.name for g in groups], ["潮汐感知", "忆潮"])
        self.assertEqual(groups[0].fields["当前等级"], "二阶")
        self.assertEqual(groups[1].fields["所属体系"], "灵能体系")

    def test_add_group_via_dialog_appends_and_saves(self):
        widget = self.widget()
        add_button = next(b for b in widget.findChildren(QPushButton) if b.text() == "＋ 添加条目")
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("静默术", True)):
            add_button.click()
        self.group_field(widget, "静默术", "所属体系").setText("灵能体系")
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        reparsed = load_canon_entry(self.path, "character")
        self.assertEqual([g.name for g in reparsed.section("能力档案").groups],
                         ["潮汐感知", "忆潮", "静默术"])

    def test_unknown_kind_rejected(self):
        widget = CanonFormWidget()
        with self.assertRaisesRegex(ValueError, "未知的设定材料类型"):
            widget.load_entry(self.path, "faction")

    def test_narrow_page_both_themes_large_font_no_horizontal_overflow(self):
        page = self.page()
        for theme in ("light", "dark"):
            apply_theme(self.app, {'theme': theme, 'ui_font_size': 18})
            page.resize(520, 560)
            page.show()
            self.app.processEvents()
            scroll = page.findChild(QScrollArea)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
            self.assertLessEqual(
                page.width(), self.app.primaryScreen().availableGeometry().width())
            page.close()

    def test_narrow_page_project_mode_both_themes_large_font_no_horizontal_overflow(self):
        project = self.fake_project()
        page = self.page(project=project, path=self.baiou)
        for theme in ("light", "dark"):
            apply_theme(self.app, {'theme': theme, 'ui_font_size': 18})
            page.resize(520, 560)
            page.show()
            self.app.processEvents()
            scroll = page.findChild(QScrollArea)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
            self.assertLessEqual(
                page.width(), self.app.primaryScreen().availableGeometry().width())
            page.close()

    # ---------- 关键关系选择器（角色卡 + 传入 project 时） ----------

    def test_relation_picker_add_and_save(self):
        project = self.fake_project()
        widget = CanonFormWidget()
        widget.load_entry(self.baiou, "character", project)
        add_button = next(b for b in widget.findChildren(QPushButton) if b.text() == "＋ 关联角色")
        with patch("ui.canon_form_widget.QInputDialog.getItem", return_value=("林舟", True)):
            add_button.click()
        row_texts = [editor.text() for _section, editor in widget._relation_rows]
        self.assertIn("- 守塔老人：师徒", row_texts)
        self.assertIn("- 林舟：", row_texts)
        self.assertTrue(widget.is_modified())
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.baiou.read_text(encoding="utf-8")
        self.assertIn("- 林舟：", saved)
        self.assertEqual(saved.count("- 守塔老人：师徒"), 1)
        reparsed = load_canon_entry(self.baiou, "character")
        self.assertEqual(reparsed.section("关键关系").text, "- 守塔老人：师徒\n- 林舟：")

    # ---------- 用户模块（Phase B：新建/重命名/上移/下移/删除） ----------

    def op_button(self, host, heading, text):
        widget = host.form if isinstance(host, StructuredCanonPage) else host
        for button in widget.findChildren(QPushButton):
            if button.text() == text and button.property("moduleHeading") == heading:
                return button
        raise AssertionError(f"找不到模块「{heading}」的「{text}」按钮")

    def test_new_module_dialog_confirm_creates_module(self):
        widget = self.widget()
        dialog = NewModuleDialog(widget)
        dialog.name_edit.setText("旅途见闻")
        dialog.confirm()
        self.assertEqual(widget.entry.section("旅途见闻").role, "text")
        self.assertTrue(widget.is_modified())
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        reparsed = load_canon_entry(self.path, "character")
        self.assertIsNotNone(reparsed.section("旅途见闻"))
        # 新模块插入在受管同步区之前
        headings = [s.heading for s in reparsed.sections]
        self.assertLess(headings.index("旅途见闻"),
                        headings.index("当前剧情状态（DeepSonder 同步）"))

    def test_new_module_dialog_group_and_fields_types(self):
        widget = self.widget()
        dialog = NewModuleDialog(widget)
        dialog.name_edit.setText("随身物品")
        dialog.role_buttons["group"].setChecked(True)
        dialog.confirm()
        self.assertEqual(widget.entry.section("随身物品").role, "group")
        dialog = NewModuleDialog(widget)
        dialog.name_edit.setText("弱点清单")
        dialog.role_buttons["fields"].setChecked(True)
        dialog.confirm()
        self.assertEqual(widget.entry.section("弱点清单").role, "fields")

    def test_new_module_dialog_duplicate_shows_error_inside(self):
        widget = self.widget()
        before = [s.heading for s in widget.entry.sections]
        dialog = NewModuleDialog(widget)
        dialog.name_edit.setText("基础档案")
        dialog.confirm()
        self.assertIn("已存在", dialog.error_label.text())
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual([s.heading for s in widget.entry.sections], before)
        self.assertFalse(widget.is_modified())

    def test_new_module_dialog_empty_name_shows_error(self):
        widget = self.widget()
        dialog = NewModuleDialog(widget)
        dialog.name_edit.setText("   ")
        dialog.confirm()
        self.assertIn("不能为空", dialog.error_label.text())

    def test_page_new_module_button_full_flow(self):
        page = self.page()

        def fake_exec(dialog):
            dialog.name_edit.setText("随身物品")
            dialog.role_buttons["group"].setChecked(True)
            dialog.confirm()
            return dialog.result()

        with patch.object(NewModuleDialog, "exec", fake_exec):
            page.add_module_button.click()
        self.assertEqual(page.form.entry.section("随身物品").role, "group")
        self.assertTrue(page.is_modified())
        # 分组型模块可继续添加条目并保存
        with patch("ui.canon_form_widget.QInputDialog.getText", return_value=("钥匙", True)):
            next(b for b in page.findChildren(QPushButton)
                 if b.text() == "＋ 添加条目"
                 and b.property("moduleHeading") == "随身物品").click()
        ok, _error = page.write_changes()
        self.assertTrue(ok)
        reparsed = load_canon_entry(self.path, "character")
        self.assertEqual(reparsed.section("随身物品").role, "group")
        self.assertEqual([g.name for g in reparsed.section("随身物品").groups], ["钥匙"])

    def test_rename_module_via_button_saves_new_heading(self):
        widget = self.widget()
        with patch("ui.canon_form_widget.QInputDialog.getText",
                   return_value=("容貌印象", True)):
            self.op_button(widget, "外貌与辨识特征", "重命名").click()
        self.assertIsNotNone(widget.entry.section("容貌印象"))
        self.assertTrue(widget.is_modified())
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("## 容貌印象", saved)
        self.assertNotIn("## 外貌与辨识特征", saved)
        self.assertIn("左手腕有旧疤。", saved)  # 正文逐字节保留

    def test_rename_module_cancel_keeps_entry(self):
        widget = self.widget()
        with patch("ui.canon_form_widget.QInputDialog.getText",
                   return_value=("", False)):
            self.op_button(widget, "外貌与辨识特征", "重命名").click()
        self.assertIsNotNone(widget.entry.section("外貌与辨识特征"))
        self.assertFalse(widget.is_modified())

    def test_rename_module_duplicate_shows_feedback(self):
        widget = self.widget()
        with patch("ui.canon_form_widget.QInputDialog.getText",
                   return_value=("基础档案", True)):
            self.op_button(widget, "外貌与辨识特征", "重命名").click()
        self.assertIn("已存在", widget.feedback.text())
        self.assertIsNotNone(widget.entry.section("外貌与辨识特征"))
        self.assertFalse(widget.is_modified())

    def test_delete_module_requires_confirm_then_removes(self):
        widget = self.widget()
        with patch("ui.canon_form_widget.QMessageBox") as message_box:
            message_box.question.return_value = message_box.StandardButton.No
            self.op_button(widget, "外貌与辨识特征", "删除").click()
        self.assertIsNotNone(widget.entry.section("外貌与辨识特征"))
        self.assertFalse(widget.is_modified())
        with patch("ui.canon_form_widget.QMessageBox") as message_box:
            message_box.question.return_value = message_box.StandardButton.Yes
            self.op_button(widget, "外貌与辨识特征", "删除").click()
        self.assertIsNone(widget.entry.section("外貌与辨识特征"))
        self.assertTrue(widget.is_modified())
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.path.read_text(encoding="utf-8")
        self.assertNotIn("## 外貌与辨识特征", saved)
        self.assertNotIn("左手腕有旧疤。", saved)
        self.assertIn("## 性格、动机与核心欲望", saved)  # 相邻区块保留

    def test_move_buttons_reorder_modules(self):
        widget = self.widget()
        self.op_button(widget, "外貌与辨识特征", "下移").click()
        headings = [s.heading for s in widget.entry.sections]
        self.assertEqual(headings[1], "性格、动机与核心欲望")
        self.assertEqual(headings[2], "外貌与辨识特征")
        self.assertTrue(widget.is_modified())
        self.op_button(widget, "外貌与辨识特征", "上移").click()
        headings = [s.heading for s in widget.entry.sections]
        self.assertEqual(headings[1], "外貌与辨识特征")
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.path.read_text(encoding="utf-8")
        self.assertLess(saved.index("## 外貌与辨识特征"),
                       saved.index("## 性格、动机与核心欲望"))

    def test_move_button_boundary_shows_feedback(self):
        widget = self.widget()
        self.op_button(widget, "外貌与辨识特征", "上移").click()  # 上面是系统区块
        self.assertIn("边界", widget.feedback.text())
        self.assertFalse(widget.is_modified())
        self.assertEqual([s.heading for s in widget.entry.sections][0], "基础档案")

    def test_system_modules_show_badge_without_op_buttons(self):
        widget = self.widget()
        for heading in ("基础档案", "当前剧情状态（DeepSonder 同步）"):
            for text in ("重命名", "上移", "下移", "删除"):
                matches = [b for b in widget.findChildren(QPushButton)
                           if b.property("moduleHeading") == heading and b.text() == text]
                self.assertEqual(matches, [], f"{heading} 不应有「{text}」按钮")
        badges = [l for l in widget.findChildren(QLabel)
                  if l.objectName() == "systemModuleBadge"]
        self.assertEqual([b.text() for b in badges], ["系统区块"])  # 仅基础档案
        # 系统区块内容仍可编辑，受管区保持只读
        self.field(widget, "姓名").setText("白羽")
        self.assertTrue(widget.is_modified())
        managed = next(s for s in widget.entry.sections if s.role == "managed")
        viewer = next(b for b in widget.form_scroll.findChildren(QPlainTextEdit)
                      if b.toPlainText().startswith(MANAGED_STATE_START.strip()))
        self.assertTrue(viewer.isReadOnly())
        self.assertNotEqual(managed.heading, "")

    def test_relation_module_keeps_picker_until_renamed(self):
        project = self.fake_project()
        widget = CanonFormWidget()
        widget.load_entry(self.baiou, "character", project)
        self.assertTrue(widget._relation_rows)  # 仍渲染关联选择器
        with patch("ui.canon_form_widget.QInputDialog.getText",
                   return_value=("人物关系", True)):
            self.op_button(widget, "关键关系", "重命名").click()
        self.assertFalse(widget._relation_rows)  # 重命名后按普通文本模块渲染
        self.assertIsNotNone(widget.entry.section("人物关系"))
        self.assertEqual(widget.entry.section("人物关系").role, "text")

    def test_narrow_world_page_with_module_ops_no_overflow(self):
        project = self.fake_project()
        page = self.page(project=project, path=self.beigang, kind="world")
        for theme in ("light", "dark"):
            apply_theme(self.app, {'theme': theme, 'ui_font_size': 18})
            page.resize(520, 560)
            page.show()
            self.app.processEvents()
            scroll = page.findChild(QScrollArea)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
            page.close()

    # ---------- on_saved 回调 ----------

    def test_save_triggers_on_saved_callback(self):
        saved = []
        widget = self.widget()
        widget.on_saved = saved.append
        self.field(widget, "姓名").setText("白羽")
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        self.assertEqual(saved, [self.path])
        self.assertIn("- 姓名：白羽", self.path.read_text(encoding="utf-8"))

    def test_save_without_change_does_not_call_on_saved(self):
        saved = []
        widget = self.widget()
        widget.on_saved = saved.append
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        self.assertEqual(saved, [])

    # ---------- 未保存确认（原 closeEvent 三选一迁移为 confirm_discard_or_save） ----------

    def test_confirm_cancel_keeps_modifications(self):
        before = self.path.read_bytes()
        widget = self.widget()
        self.field(widget, "姓名").setText("白羽")
        self._patch_confirm_prompt("取消")
        self.assertFalse(widget.confirm_discard_or_save())
        self.assertTrue(widget.is_modified())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.field(widget, "姓名").text(), "白羽")

    def test_confirm_discard_reloads_from_disk_without_write(self):
        before = self.path.read_bytes()
        widget = self.widget()
        self.field(widget, "姓名").setText("白羽")
        self._patch_confirm_prompt("放弃修改")
        self.assertTrue(widget.confirm_discard_or_save())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(widget.is_modified())
        # 放弃后表单回到磁盘版本
        self.assertEqual(self.field(widget, "姓名").text(), "白鸥")

    def test_confirm_save_writes_and_resets_modified(self):
        saved = []
        widget = self.widget()
        widget.on_saved = saved.append
        self.field(widget, "姓名").setText("白羽")
        self._patch_confirm_prompt("保存")
        self.assertTrue(widget.confirm_discard_or_save())
        self.assertIn("- 姓名：白羽", self.path.read_text(encoding="utf-8"))
        self.assertFalse(widget.is_modified())
        self.assertEqual(saved, [self.path])

    def test_confirm_without_modifications_allows_without_prompt(self):
        widget = self.widget()
        with patch("ui.canon_form_widget.QMessageBox") as message_box:
            self.assertTrue(widget.confirm_discard_or_save())
            message_box.assert_not_called()

    # ---------- open_path 切换流程（页面级） ----------

    def test_switch_entry_discard_flow(self):
        project = self.fake_project()
        page = self.page(project=project, path=self.baiou)
        before = self.baiou.read_bytes()
        self.field(page, "姓名").setText("白鸥改")
        self.assertTrue(page.is_modified())
        self._patch_confirm_prompt("放弃修改")
        self.assertTrue(page.open_path(self.linzhou, "character", project))
        self.assertEqual(page.current_path(), self.linzhou)
        self.assertEqual(page.current_kind(), "character")
        self.assertEqual(self.baiou.read_bytes(), before)
        self.assertFalse(page.is_modified())
        reparsed = load_canon_entry(self.linzhou, "character")
        self.assertEqual(reparsed.section("基础档案").fields["姓名"], "林舟")

    def test_switch_entry_cancel_keeps_old_entry(self):
        project = self.fake_project()
        page = self.page(project=project, path=self.baiou)
        before = self.baiou.read_bytes()
        self.field(page, "姓名").setText("白鸥改")
        self._patch_confirm_prompt("取消")
        self.assertFalse(page.open_path(self.linzhou, "character", project))
        self.assertEqual(page.current_path(), self.baiou)
        self.assertTrue(page.is_modified())
        self.assertEqual(self.baiou.read_bytes(), before)

    def test_switch_entry_save_flow_writes_old_entry(self):
        project = self.fake_project()
        page = self.page(project=project, path=self.baiou)
        self.field(page, "姓名").setText("白鸥改")
        self._patch_confirm_prompt("保存")
        self.assertTrue(page.open_path(self.linzhou, "character", project))
        self.assertIn("- 姓名：白鸥改", self.baiou.read_text(encoding="utf-8"))
        self.assertEqual(page.current_path(), self.linzhou)
        self.assertFalse(page.is_modified())

    def test_open_same_path_short_circuits_without_prompt(self):
        project = self.fake_project()
        page = self.page(project=project, path=self.baiou)
        with patch("ui.canon_form_widget.QMessageBox") as message_box:
            self.assertTrue(page.open_path(self.baiou, "character", project))
            message_box.assert_not_called()
        self.assertEqual(page.current_path(), self.baiou)

    def test_open_path_load_failure_keeps_old_entry(self):
        project = self.fake_project()
        page = self.page(project=project, path=self.baiou)
        with self.assertRaises(OSError):
            page.open_path(project.canon_dir / "characters" / "不存在.md", "character", project)
        self.assertEqual(page.current_path(), self.baiou)

    # ---------- 页头（材料名 / 类型徽章 / 修改状态 / 保存按钮 / saved 信号） ----------

    def test_page_header_shows_title_kind_badge_and_modified_state(self):
        page = self.page()
        self.assertEqual(page.title_label.text(), "白鸥")
        self.assertEqual(page.kind_badge.text(), "角色")
        self.assertTrue(page.kind_badge.isVisibleTo(page))
        self.assertEqual(page.modified_badge.text(), "已保存")
        self.field(page, "姓名").setText("白羽")
        self.assertEqual(page.modified_badge.text(), "未保存修改")
        self.assertEqual(page.modified_badge.objectName(), "dirtyBadge")
        ok, _error = page.write_changes()
        self.assertTrue(ok)
        self.assertEqual(page.modified_badge.text(), "已保存")
        self.assertEqual(page.modified_badge.objectName(), "savedBadge")

    def test_page_save_button_writes_and_emits_saved_signal(self):
        emitted = []
        page = self.page()
        page.saved.connect(lambda path: emitted.append(path))
        self.field(page, "姓名").setText("白羽")
        page.save_button.click()
        self.assertIn("- 姓名：白羽", self.path.read_text(encoding="utf-8"))
        self.assertEqual(emitted, [str(self.path)])
        self.assertFalse(page.is_modified())

    def test_reset_clears_page_state(self):
        page = self.page()
        self.field(page, "姓名").setText("白羽")
        page.reset()
        self.assertIsNone(page.current_path())
        self.assertIsNone(page.current_kind())
        self.assertFalse(page.is_modified())
        self.assertEqual(page.title_label.text(), "未选择设定材料")
        self.assertFalse(page.kind_badge.isVisibleTo(page))
        self.assertFalse(page.save_button.isEnabled())

    def test_unknown_kind_rejected_via_page(self):
        page = StructuredCanonPage()
        with self.assertRaisesRegex(ValueError, "未知的设定材料类型"):
            page.open_path(self.path, "faction")


if __name__ == "__main__":
    unittest.main()
