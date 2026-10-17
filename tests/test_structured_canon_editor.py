import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea
from PySide6.QtGui import QFontDatabase
from core.canon_schema import SECTION_START, load_canon_entry
from core.character_card_sync import MANAGED_STATE_START, MANAGED_STATE_END
from core.project_data import CANON_ENTRY_TYPES
from tests.test_canon_index import FakeProject
from tests.test_canon_schema import character_text
from ui.structured_canon_editor import StructuredCanonEditor, MODE_SOURCE
from ui.theme import apply_theme


class StructuredCanonEditorTests(unittest.TestCase):
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

    def dialog(self):
        dialog = StructuredCanonEditor(self.path, "character")
        self.addCleanup(dialog.close)
        return dialog

    def field(self, dialog, key):
        return next(e for section, k, e in dialog._field_widgets if k == key)

    def group_field(self, dialog, group_name, key):
        return next(e for s, g, k, e in dialog._group_widgets if g == group_name and k == key)

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

    def project_dialog(self, project, path=None, kind="character"):
        dialog = StructuredCanonEditor(path or self.baiou, kind, project=project)
        self.addCleanup(dialog.close)
        return dialog

    def index_item(self, dialog, path):
        for row in range(dialog._index_list.count()):
            data = dialog._index_list.item(row).data(Qt.ItemDataRole.UserRole)
            if data and Path(data["path"]) == Path(path):
                return dialog._index_list.item(row)
        return None

    def test_form_edit_marks_section_and_preserves_rest_bytes(self):
        dialog = self.dialog()
        self.field(dialog, "身份／阵营").setText("北港向导")
        dialog.save()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn(SECTION_START, saved)
        self.assertIn("- 身份／阵营：北港向导", saved)
        self.assertIn("- 别名：林舟", saved)
        self.assertIn("临时想法。", saved)
        self.assertIn(MANAGED_STATE_START, saved)
        reparsed = load_canon_entry(self.path, "character")
        self.assertEqual(reparsed.section("当前剧情状态（DeepSonder 同步）").role, "managed")

    def test_save_without_change_writes_nothing(self):
        before = self.path.read_bytes()
        dialog = self.dialog()
        dialog.save()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        self.assertEqual(self.path.read_bytes(), before)

    def test_revision_lock_rejects_external_modification(self):
        dialog = self.dialog()
        self.field(dialog, "姓名").setText("白鸥改名")
        external = character_text().replace("谨慎。", "外部修改后的性格。")
        self.path.write_text(external, encoding="utf-8")
        dialog.save()
        self.assertNotEqual(dialog.result(), dialog.DialogCode.Accepted)
        self.assertIn("重新打开", dialog.feedback.text())
        self.assertEqual(self.path.read_text(encoding="utf-8"), external)

    def test_source_mode_edit_writes_through_and_form_reparses(self):
        dialog = self.dialog()
        dialog._mode_buttons[MODE_SOURCE].click()
        self.assertEqual(dialog.stack.currentIndex(), 1)
        dialog.source_edit.setPlainText(dialog.source_edit.toPlainText() + "\n## 附记\n\n手写追加。\n")
        dialog.save()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("## 附记", saved)
        reparsed = load_canon_entry(self.path, "character")
        self.assertEqual(reparsed.section("附记").role, "raw")

    def test_managed_section_is_readonly_without_field_widgets(self):
        dialog = self.dialog()
        managed = next(s for s in dialog.entry.sections if s.role == "managed")
        self.assertFalse([w for s, k, w in dialog._field_widgets if s is managed])
        box = next(b for b in dialog.form_scroll.findChildren(QPlainTextEdit)
                   if managed.heading and b.toPlainText().startswith(MANAGED_STATE_START.strip()))
        self.assertTrue(box.isReadOnly())

    def test_group_field_edit_persists(self):
        dialog = self.dialog()
        self.group_field(dialog, "潮汐感知", "当前等级").setText("二阶")
        dialog.save()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        saved = self.path.read_text(encoding="utf-8")
        self.assertIn("- 当前等级：二阶", saved)
        reparsed = load_canon_entry(self.path, "character")
        groups = reparsed.section("能力档案").groups
        self.assertEqual([g.name for g in groups], ["潮汐感知", "忆潮"])
        self.assertEqual(groups[0].fields["当前等级"], "二阶")
        self.assertEqual(groups[1].fields["所属体系"], "灵能体系")

    def test_add_group_via_dialog_appends_and_saves(self):
        dialog = self.dialog()
        add_button = next(b for b in dialog.findChildren(QPushButton) if b.text() == "＋ 添加条目")
        with patch("ui.structured_canon_editor.QInputDialog.getText", return_value=("静默术", True)):
            add_button.click()
        self.group_field(dialog, "静默术", "所属体系").setText("灵能体系")
        dialog.save()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        reparsed = load_canon_entry(self.path, "character")
        self.assertEqual([g.name for g in reparsed.section("能力档案").groups],
                         ["潮汐感知", "忆潮", "静默术"])

    def test_unknown_kind_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知的设定材料类型"):
            StructuredCanonEditor(self.path, "faction")

    def test_narrow_window_both_themes_large_font_no_horizontal_overflow(self):
        for theme in ("light", "dark"):
            apply_theme(self.app, {'theme': theme, 'ui_font_size': 18})
            dialog = self.dialog()
            dialog.resize(520, 560)
            dialog.show()
            self.app.processEvents()
            scroll = dialog.findChild(QScrollArea)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
            self.assertLessEqual(dialog.width(), self.app.primaryScreen().availableGeometry().width())
            dialog.close()

    def test_sidebar_lists_badges_and_filters(self):
        project = self.fake_project()
        dialog = self.project_dialog(project)
        self.assertEqual(dialog._index_list.count(), 3)
        baiou = self.index_item(dialog, self.baiou)
        linzhou = self.index_item(dialog, self.linzhou)
        self.assertIn("已同步", baiou.text())
        self.assertIn("缺", linzhou.text())
        self.assertIs(dialog._index_list.currentItem(), baiou)
        dialog._filter_buttons["character"].click()
        self.assertEqual(dialog._index_list.count(), 2)
        self.assertIs(dialog._index_list.currentItem(), self.index_item(dialog, self.baiou))
        dialog._filter_buttons["all"].click()
        self.assertEqual(dialog._index_list.count(), 3)

    def test_identity_badge_from_registry(self):
        project = self.fake_project()
        registry = {"schema_version": 1, "operations": [],
                    "groups": [{"id": "g1", "primary": "白鸥", "names": ["白鸥", "林舟"],
                                "from_chapter": 1, "choices": {}}]}
        (project.memory_dir / "character_identities.json").write_text(
            json.dumps(registry, ensure_ascii=False), encoding="utf-8")
        dialog = self.project_dialog(project)
        self.assertIn("身份组", self.index_item(dialog, self.baiou).text())
        self.assertIn("别名", self.index_item(dialog, self.linzhou).text())

    def test_switch_entry_discard_flow(self):
        project = self.fake_project()
        dialog = self.project_dialog(project)
        before = self.baiou.read_bytes()
        self.field(dialog, "姓名").setText("白鸥改")
        self.assertTrue(dialog._modified)
        linzhou = self.index_item(dialog, self.linzhou)
        with patch("ui.structured_canon_editor.QMessageBox") as message_box:
            box = message_box.return_value
            buttons = {}

            def fake_add(text, *args, **kwargs):
                button = MagicMock()
                buttons[text] = button
                return button

            box.addButton.side_effect = fake_add
            box.clickedButton.side_effect = lambda: buttons["放弃修改"]
            dialog._index_list.itemClicked.emit(linzhou)
        self.assertEqual(dialog.path, self.linzhou)
        self.assertEqual(dialog.kind, "character")
        self.assertEqual(self.baiou.read_bytes(), before)
        self.assertFalse(dialog._modified)
        self.assertIs(dialog._index_list.currentItem(), self.index_item(dialog, self.linzhou))
        reparsed = load_canon_entry(self.linzhou, "character")
        self.assertEqual(reparsed.section("基础档案").fields["姓名"], "林舟")

    def test_relation_picker_add_and_save(self):
        project = self.fake_project()
        dialog = self.project_dialog(project)
        add_button = next(b for b in dialog.findChildren(QPushButton) if b.text() == "＋ 关联角色")
        with patch("ui.structured_canon_editor.QInputDialog.getItem", return_value=("林舟", True)):
            add_button.click()
        row_texts = [editor.text() for _section, editor in dialog._relation_rows]
        self.assertIn("- 守塔老人：师徒", row_texts)
        self.assertIn("- 林舟：", row_texts)
        self.assertTrue(dialog._modified)
        dialog.save()
        self.assertEqual(dialog.result(), dialog.DialogCode.Accepted)
        saved = self.baiou.read_text(encoding="utf-8")
        self.assertIn("- 林舟：", saved)
        self.assertEqual(saved.count("- 守塔老人：师徒"), 1)
        reparsed = load_canon_entry(self.baiou, "character")
        self.assertEqual(reparsed.section("关键关系").text, "- 守塔老人：师徒\n- 林舟：")

    def test_narrow_window_project_mode_both_themes_large_font_no_horizontal_overflow(self):
        project = self.fake_project()
        for theme in ("light", "dark"):
            apply_theme(self.app, {'theme': theme, 'ui_font_size': 18})
            dialog = self.project_dialog(project)
            dialog.resize(520, 560)
            dialog.show()
            self.app.processEvents()
            scroll = dialog.findChild(QScrollArea)
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
            self.assertLessEqual(dialog.width(), self.app.primaryScreen().availableGeometry().width())
            dialog.close()


if __name__ == "__main__":
    unittest.main()
