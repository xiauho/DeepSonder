import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import (QApplication, QDialog, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QScrollArea, QToolButton)
from PySide6.QtGui import QFontDatabase
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from core.canon_schema import SECTION_START, load_canon_entry
from core.character_card_sync import MANAGED_STATE_START
from core.project_data import CANON_ENTRY_TYPES
from tests.test_canon_index import FakeProject
from tests.test_canon_schema import character_text
from ui.canon_form_widget import CanonFormWidget, NewModuleDialog
from ui.structured_canon_page import StructuredCanonPage
from ui.canon_sync_view import CanonSyncView
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
        self._existing_windows = {id(widget) for widget in self.app.topLevelWidgets()}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.canon = Path(self.tmp.name) / "canon" / "characters"
        self.canon.mkdir(parents=True)
        self.path = self.canon / "白鸥.md"
        self.path.write_text(character_text(), encoding="utf-8")
        self.addCleanup(lambda: apply_theme(self.app, {'theme': 'light'}))

    def tearDown(self):
        # Theme changes must not keep restyling windows from earlier cases.
        for widget in self.app.topLevelWidgets():
            if id(widget) not in self._existing_windows:
                widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

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

    def raw_editor(self, host, heading):
        widget = host.form if isinstance(host, StructuredCanonPage) else host
        return next(e for s, e in widget._raw_widgets if s.heading == heading)

    def extras_rows(self, host, heading, group_name=None):
        widget = host.form if isinstance(host, StructuredCanonPage) else host
        return next(rows for s, g, rows in widget._extras_rows
                    if s.heading == heading and g == group_name)

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
        self.assertIn("重新载入", error)
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)
        self.assertTrue(widget.is_modified())

    def test_deleted_file_save_reports_error_without_crash(self):
        widget = self.widget()
        self.field(widget, "姓名").setText("白鸥改名")
        self.path.unlink()
        ok, error = widget.write_changes()
        self.assertFalse(ok)
        self.assertIn("重新载入", error)

    # ---------- Phase C：仅表单视图，原文区块与手写行可编辑 ----------

    def test_widget_exposes_no_mode_buttons(self):
        widget = self.widget()
        mode_texts = {"表单", "源码", "预览"}
        for button in [*widget.findChildren(QPushButton), *widget.findChildren(QToolButton)]:
            self.assertNotIn(button.text(), mode_texts)

    def test_raw_block_edit_saves_verbatim_and_keeps_other_sections(self):
        widget = self.widget()
        editor = self.raw_editor(widget, "作者自由备注")
        self.assertFalse(editor.isReadOnly())
        self.assertEqual(editor.toPlainText(), "临时想法。")
        editor.setPlainText("新的想法。\n- 保留原有列表格式")
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.path.read_text(encoding="utf-8")
        original = character_text()
        # 其余区块逐字节不变，原文区块正文逐字写入
        self.assertTrue(saved.startswith(original[:original.index("## 作者自由备注")]))
        self.assertIn("## 作者自由备注\n\n新的想法。\n- 保留原有列表格式\n", saved)
        reparsed = load_canon_entry(self.path, "character")
        self.assertEqual(reparsed.section("作者自由备注").role, "free")
        # 重新载入后不做修改再保存：零写入，字节不变
        before = self.path.read_bytes()
        widget2 = self.widget()
        ok, _error = widget2.write_changes()
        self.assertTrue(ok)
        self.assertEqual(self.path.read_bytes(), before)

    def test_raw_module_block_editable_with_badge(self):
        self.path.write_text(
            character_text() + "\n## 对照表\n\n| a | b |\n| --- | --- |\n",
            encoding="utf-8")
        widget = self.widget()
        badges = [l for l in widget.findChildren(QLabel)
                  if l.objectName() == "rawModuleBadge"]
        self.assertEqual([b.text() for b in badges], ["原文区块", "原文区块"])
        captions = [l for l in widget.findChildren(QLabel)
                    if l.text() == "该区块保留原文格式，修改后原样写入文档。"]
        self.assertEqual(len(captions), 2)
        editor = self.raw_editor(widget, "对照表")
        editor.setPlainText("| a | c |")
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("## 对照表\n\n| a | c |\n", saved)
        reparsed = load_canon_entry(self.path, "character")
        self.assertEqual(reparsed.section("对照表").role, "raw")

    def test_extras_rows_edit_round_trip(self):
        self.path.write_text(character_text().replace(
            "- 首次出场：chapter_01\n",
            "- 首次出场：chapter_01\n手写备注行。\n"), encoding="utf-8")
        widget = self.widget()
        rows = self.extras_rows(widget, "基础档案")
        self.assertEqual([e.text() for e in rows], ["手写备注行。"])
        rows[0].setText("手写备注行（修改后）。")
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("- 首次出场：chapter_01\n手写备注行（修改后）。\n", saved)
        self.assertNotIn(SECTION_START, saved)  # 手写行编辑不引入受管标记
        reparsed = load_canon_entry(self.path, "character")
        self.assertEqual(reparsed.section("基础档案").extras, ["手写备注行（修改后）。"])
        self.assertEqual(reparsed.section("基础档案").fields["姓名"], "白鸥")

    def test_group_extras_rows_edit_round_trip(self):
        self.path.write_text(character_text().replace(
            "- 能力属性：记忆\n",
            "- 能力属性：记忆\n忆潮每晚自动触发（手写）。\n"), encoding="utf-8")
        widget = self.widget()
        rows = self.extras_rows(widget, "能力档案", "忆潮")
        rows[0].setText("触发条件：满月（手写改）。")
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("- 能力属性：记忆\n触发条件：满月（手写改）。\n", saved)
        self.assertNotIn(SECTION_START, saved)
        reparsed = load_canon_entry(self.path, "character")
        groups = reparsed.section("能力档案").groups
        self.assertEqual([g.name for g in groups], ["潮汐感知", "忆潮"])
        self.assertEqual(next(g.extras for g in groups if g.name == "忆潮"),
                         ["触发条件：满月（手写改）。"])

    def test_form_renders_all_module_types(self):
        self.path.write_text(
            character_text() + "\n## 对照表\n\n| a | b |\n| --- | --- |\n",
            encoding="utf-8")
        widget = self.widget()
        self.assertTrue(widget._field_widgets)   # 字段型
        self.assertTrue(widget._group_widgets)   # 分组型
        self.assertTrue(widget._text_widgets)    # 文本型
        self.assertEqual([(s.heading, s.role) for s, _e in widget._raw_widgets],
                         [("作者自由备注", "free"), ("对照表", "raw")])
        self.assertEqual([e.toPlainText() for s, e in widget._raw_widgets
                          if s.heading == "作者自由备注"], ["临时想法。"])

    def test_managed_section_is_readonly_without_field_widgets(self):
        widget = self.widget()
        managed = next(s for s in widget.entry.sections if s.role == "managed")
        self.assertFalse([w for s, k, w in widget._field_widgets if s is managed])
        view = widget.findChild(CanonSyncView)
        self.assertEqual(view.findChildren(QLineEdit), [])
        self.assertEqual(view.findChildren(QPlainTextEdit), [])
        self.assertEqual(view.value_labels["location"].text(), "北港")

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

    def op_action(self, host, heading, text):
        """模块结构操作已收敛到 ⋯ 菜单；返回对应动作供 trigger()。"""
        widget = host.form if isinstance(host, StructuredCanonPage) else host
        for button in widget.findChildren(QToolButton):
            if (button.objectName() != "moduleMenuButton"
                    or button.property("moduleHeading") != heading):
                continue
            for action in button.menu().actions():
                if action.text() == text:
                    return action
        raise AssertionError(f"找不到模块「{heading}」的「{text}」操作")

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
            self.op_action(widget, "外貌与辨识特征", "重命名模块…").trigger()
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
            self.op_action(widget, "外貌与辨识特征", "重命名模块…").trigger()
        self.assertIsNotNone(widget.entry.section("外貌与辨识特征"))
        self.assertFalse(widget.is_modified())

    def test_rename_module_duplicate_shows_feedback(self):
        widget = self.widget()
        with patch("ui.canon_form_widget.QInputDialog.getText",
                   return_value=("基础档案", True)):
            self.op_action(widget, "外貌与辨识特征", "重命名模块…").trigger()
        self.assertIn("已存在", widget.feedback.text())
        self.assertIsNotNone(widget.entry.section("外貌与辨识特征"))
        self.assertFalse(widget.is_modified())

    def test_delete_module_requires_confirm_then_removes(self):
        widget = self.widget()
        with patch("ui.canon_form_widget.QMessageBox") as message_box:
            message_box.question.return_value = message_box.StandardButton.No
            self.op_action(widget, "外貌与辨识特征", "删除模块…").trigger()
        self.assertIsNotNone(widget.entry.section("外貌与辨识特征"))
        self.assertFalse(widget.is_modified())
        with patch("ui.canon_form_widget.QMessageBox") as message_box:
            message_box.question.return_value = message_box.StandardButton.Yes
            self.op_action(widget, "外貌与辨识特征", "删除模块…").trigger()
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
        self.op_action(widget, "外貌与辨识特征", "下移").trigger()
        headings = [s.heading for s in widget.entry.sections]
        self.assertEqual(headings[1], "性格、动机与核心欲望")
        self.assertEqual(headings[2], "外貌与辨识特征")
        self.assertTrue(widget.is_modified())
        self.op_action(widget, "外貌与辨识特征", "上移").trigger()
        headings = [s.heading for s in widget.entry.sections]
        self.assertEqual(headings[1], "外貌与辨识特征")
        ok, _error = widget.write_changes()
        self.assertTrue(ok)
        saved = self.path.read_text(encoding="utf-8")
        self.assertLess(saved.index("## 外貌与辨识特征"),
                       saved.index("## 性格、动机与核心欲望"))

    def test_move_button_boundary_shows_feedback(self):
        widget = self.widget()
        self.op_action(widget, "外貌与辨识特征", "上移").trigger()  # 上面是系统区块
        self.assertIn("边界", widget.feedback.text())
        self.assertFalse(widget.is_modified())
        self.assertEqual([s.heading for s in widget.entry.sections][0], "基础档案")

    def test_system_modules_show_badge_without_module_menu(self):
        widget = self.widget()
        for heading in ("基础档案", "当前剧情状态（DeepSonder 同步）"):
            menus = [b for b in widget.findChildren(QToolButton)
                     if b.objectName() == "moduleMenuButton"
                     and b.property("moduleHeading") == heading]
            self.assertEqual(menus, [], f"{heading} 不应有模块操作菜单")
        badges = [l for l in widget.findChildren(QLabel)
                  if l.objectName() == "systemModuleBadge"]
        self.assertEqual([b.text() for b in badges], ["结构固定", "自动同步·只读"])
        # 系统区块内容仍可编辑，受管区保持只读
        self.field(widget, "姓名").setText("白羽")
        self.assertTrue(widget.is_modified())
        managed = next(s for s in widget.entry.sections if s.role == "managed")
        viewer = widget.findChild(CanonSyncView)
        self.assertEqual(viewer.findChildren(QLineEdit), [])
        self.assertEqual(viewer.findChildren(QPlainTextEdit), [])
        self.assertNotEqual(managed.heading, "")

    def test_relation_module_keeps_picker_after_rename(self):
        project = self.fake_project()
        widget = CanonFormWidget()
        widget.load_entry(self.baiou, "character", project)
        self.assertTrue(widget._relation_rows)  # 仍渲染关联选择器
        with patch("ui.canon_form_widget.QInputDialog.getText",
                   return_value=("人物关系", True)):
            self.op_action(widget, "关键关系", "重命名模块…").trigger()
        self.assertTrue(widget._relation_rows)  # 展示标题变化不改变关联语义
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

    def test_clicking_module_heading_row_toggles_collapse(self):
        widget = self.widget()
        section = widget.entry.section("外貌与辨识特征")
        toggle = widget._module_toggles[section.module_id]
        heading = next(w for w, t in widget._heading_toggles.items() if t is toggle)
        self.assertTrue(toggle.isChecked())
        QTest.mouseClick(heading, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(toggle.isChecked())
        QTest.mouseClick(heading, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(toggle.isChecked())
        self.assertFalse(widget.is_modified())  # 折叠只是视图状态，不产生修改

    def test_collapse_state_seeded_from_loader_without_writeback(self):
        entry = load_canon_entry(self.path, "character")
        target = next(s for s in entry.sections if s.heading == "外貌与辨识特征")
        saved = []
        widget = CanonFormWidget()
        widget.collapse_state_loader = lambda path: {target.module_id}
        widget.collapse_state_saver = lambda path, ids: saved.append(set(ids))
        widget.load_entry(self.path, "character")
        self.assertFalse(widget._module_toggles[target.module_id].isChecked())
        other = next(s for s in widget.entry.sections if s.heading == "性格、动机与核心欲望")
        self.assertTrue(widget._module_toggles[other.module_id].isChecked())
        self.assertEqual(saved, [])  # 初次构建只是恢复状态，不回写偏好
        self.assertFalse(widget.is_modified())

    def test_collapse_toggle_notifies_saver_and_drops_stale_ids(self):
        entry = load_canon_entry(self.path, "character")
        target = next(s for s in entry.sections if s.heading == "外貌与辨识特征")
        saved = []
        widget = CanonFormWidget()
        widget.collapse_state_loader = lambda path: {target.module_id, "stale-module-id"}
        widget.collapse_state_saver = lambda path, ids: saved.append(set(ids))
        widget.load_entry(self.path, "character")
        other = next(s for s in widget.entry.sections if s.heading == "性格、动机与核心欲望")
        widget._module_toggles[other.module_id].setChecked(False)
        # 失效 id（已重命名模块）不持久化；其余折叠状态完整回写
        self.assertEqual(saved[-1], {target.module_id, other.module_id})
        widget._module_toggles[target.module_id].setChecked(True)
        self.assertEqual(saved[-1], {other.module_id})
        widget._module_toggles[other.module_id].setChecked(True)
        self.assertEqual(saved[-1], set())  # 全部展开 → 清除记录

    def test_empty_sections_show_writing_guidance(self):
        widget = self.widget()
        text_editor = next(e for s, e in widget._text_widgets
                           if s.heading == "外貌与辨识特征")
        self.assertIn("外观细节", text_editor.placeholderText())
        name_field = next(e for _s, k, e in widget._field_widgets if k == "姓名")
        self.assertIn("主名", name_field.placeholderText())

    def test_auto_save_flushes_draft_and_stops_timer(self):
        page = self.page()
        page.configure_auto_save(True, 5)
        name = next(e for _s, k, e in page.form._field_widgets if k == "姓名")
        name.setText("自动写盘")
        self.assertTrue(page.is_modified())
        self.assertTrue(page._auto_save_timer.isActive())
        page._auto_save_flush()
        self.assertFalse(page.is_modified())
        self.assertFalse(page._auto_save_timer.isActive())
        self.assertIn("自动写盘", self.path.read_text(encoding="utf-8"))

    def test_prepare_leave_with_auto_save_skips_confirmation(self):
        page = self.page()
        page.configure_auto_save(True, 5)
        name = next(e for _s, k, e in page.form._field_widgets if k == "姓名")
        name.setText("离开前写盘")
        with patch("ui.canon_form_widget.QMessageBox") as message_box:
            self.assertTrue(page.prepare_leave())
            message_box.assert_not_called()
        self.assertFalse(page.is_modified())
        self.assertIn("离开前写盘", self.path.read_text(encoding="utf-8"))

    def test_prepare_leave_without_auto_save_keeps_confirmation(self):
        page = self.page()
        name = next(e for _s, k, e in page.form._field_widgets if k == "姓名")
        name.setText("等待确认")
        with patch.object(page, "confirm_discard_or_save", return_value=False) as confirm:
            self.assertFalse(page.prepare_leave())
            confirm.assert_called_once()
        self.assertTrue(page.is_modified())

    # ---------- 页内大纲 ----------

    def test_outline_entries_track_live_input(self):
        page = self.page()
        entries = page.outline_entries()
        self.assertEqual(entries[0], ("", "资料导语", False))
        self.assertEqual(entries[1][1], "基础档案")
        self.assertTrue(all(filled for _id, _heading, filled in entries[1:]))
        # 清空全部基础档案字段与一个文本分区后立即反映，不必等到保存
        for _section, _key, editor in page.form._field_widgets:
            editor.setText("")
        page.form._text_widgets[0][1].setPlainText("")
        refreshed = {heading: filled for _id, heading, filled in page.outline_entries()}
        self.assertFalse(refreshed["基础档案"])
        self.assertFalse(refreshed["外貌与辨识特征"])

    def test_outline_list_shows_sections_and_jumps_to_module(self):
        page = self.page()
        page.resize(1100, 700)
        page.show()
        self.app.processEvents()
        self.assertTrue(page.outline_panel.isVisible())
        self.assertEqual(page.outline_list.count(), len(page.outline_entries()))
        self.assertTrue(page.outline_list.item(0).text().startswith("○"))
        self.assertTrue(page.outline_list.item(1).text().startswith("●"))
        target = page.form.entry.section("秘密与人物弧线")
        page.form._module_toggles[target.module_id].setChecked(False)
        self.assertFalse(page.form._module_toggles[target.module_id].isChecked())
        row = next(index for index in range(page.outline_list.count())
                   if page.outline_list.item(index).data(Qt.ItemDataRole.UserRole)
                   == target.module_id)
        page._on_outline_item_clicked(page.outline_list.item(row))
        self.app.processEvents()
        self.assertTrue(page.form._module_toggles[target.module_id].isChecked())
        self.assertEqual(page._outline_current, target.module_id)
        page.close()

    def test_outline_hides_below_threshold_and_uses_popup(self):
        page = self.page()
        page.resize(640, 620)
        page.show()
        self.app.processEvents()
        self.assertFalse(page.outline_panel.isVisible())
        self.assertFalse(page.outline_toggle.isChecked())
        with patch("ui.structured_canon_page.QMenu") as menu:
            page.outline_toggle.click()
            self.assertTrue(menu.return_value.exec.called)
            labels = [call.args[0] for call in menu.return_value.addAction.call_args_list]
        self.assertEqual(len(labels), len(page.outline_entries()))
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
        self.assertTrue(page.modified_badge.isVisibleTo(page))
        self.assertEqual(page.modified_badge.text(), "已保存")
        self.field(page, "姓名").setText("白羽")
        self.assertTrue(page.modified_badge.isVisibleTo(page))
        self.assertEqual(page.modified_badge.text(), "待保存")
        self.assertEqual(page.modified_badge.objectName(), "dirtyBadge")
        ok, _error = page.write_changes()
        self.assertTrue(ok)
        self.assertTrue(page.modified_badge.isVisibleTo(page))
        self.assertEqual(page.modified_badge.text(), "保存成功")

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
