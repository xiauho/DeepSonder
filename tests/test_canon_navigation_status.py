import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from core.canon_schema import load_canon_entry
from core.character_card_sync import MANAGED_STATE_START, MANAGED_STATE_END
from core.project import NovelProject
from tests.test_canon_schema import character_text
from ui.left_panel import LeftPanel


class CanonNavigationStatusTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = NovelProject.create(Path(self.tmp.name) / "project", "资料状态测试")
        self.card = self.project.canon_dir / "characters" / "白鸥.md"
        self.card.write_text(character_text(), encoding="utf-8")
        self.world = self.project.canon_dir / "world" / "港口.md"
        self.world.write_text("# 港口\n\n## 核心规则\n\n规则\n", encoding="utf-8")
        self.panel = LeftPanel()
        self.panel.resize(280, 680)
        self.panel.set_project(self.project)
        self.panel.set_scope("canon")
        self.panel.show(); self.app.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.panel.close(); self.panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def item(self, path):
        return next(i for i in self.panel.document_items() if i.data(0, self.panel.PATH_ROLE) == str(path))

    def test_missing_sync_and_world_states_are_visible_in_compact_and_wide_views(self):
        card = self.item(self.card); world = self.item(self.world)
        self.assertIn("已同步", card.text(0))
        self.assertIn("缺6节", world.text(0))
        self.assertIn("基础档案：已填写", card.toolTip(0))
        self.panel.resize(580, 680); self.app.processEvents()
        self.assertEqual(card.text(0), "白鸥")
        self.assertEqual(card.text(1), "已同步")
        self.assertEqual(world.text(1), "缺6节")
        self.assertFalse(self.panel.tree.isColumnHidden(1))
        self.assertGreaterEqual(self.panel.tree.columnWidth(1), self.panel.tree.fontMetrics().horizontalAdvance("已同步") + 30)

    def test_refresh_changes_status_without_opening_file_or_losing_navigation(self):
        self.panel.search.setText("白鸥")
        self.panel.select_path(self.card)
        self.panel.search.setText("白鸥")
        source = "# 白鸥\n\n## 基础档案\n\n- 姓名：白鸥\n"
        self.card.write_text(source, encoding="utf-8")
        before = self.card.read_bytes()
        self.panel.set_project(self.project)
        self.assertIn("缺5项", self.item(self.card).text(0))
        self.assertIn("待同步", self.item(self.card).toolTip(0))
        self.assertEqual(self.panel.search.text(), "白鸥")
        self.assertEqual(self.panel.tree.currentItem().data(0, self.panel.PATH_ROLE), str(self.card))
        self.assertEqual(self.card.read_bytes(), before)

    def test_ambiguous_and_invalid_documents_show_actionable_status_without_crash(self):
        self.card.write_text("# 白鸥\n\n## 基础档案\n- 姓名：甲\n- 姓名：乙\n", encoding="utf-8")
        self.world.write_bytes(b"\xffbroken")
        self.panel.set_project(self.project)
        self.assertIn("待核对", self.item(self.card).text(0))
        self.assertIn("结构歧义", self.item(self.card).toolTip(0))
        self.assertIn("读取失败", self.item(self.world).text(0))
        self.assertEqual(self.world.read_bytes(), b"\xffbroken")

    def test_identity_hints_refresh_without_reparsing_unchanged_documents(self):
        alias = self.card.with_name("林舟.md")
        alias.write_text("# 林舟\n\n## 基础档案\n- 姓名：林舟\n", encoding="utf-8")
        self.panel.set_project(self.project)
        registry = {"schema_version":1,"operations":[],"groups":[{
            "id":"test-group", "primary":"白鸥", "names":["白鸥","林舟"],
            "from_chapter":1,"choices":{}}]}
        (self.project.memory_dir / "character_identities.json").write_text(json.dumps(registry,ensure_ascii=False),encoding="utf-8")
        with patch("ui.left_panel.load_canon_entry", wraps=load_canon_entry) as parse:
            self.panel.set_project(self.project)
            parse.assert_not_called()
        self.assertIn("主身份", self.item(self.card).text(0))
        self.assertIn("别名", self.item(alias).text(0))
        self.assertIn("归属「白鸥」", self.item(alias).toolTip(0))
        self.panel.resize(580, 680); self.app.processEvents()
        self.assertIn("别名", self.item(alias).text(0))

    def test_cached_status_only_reparses_changed_file_and_keeps_system_policy(self):
        self.world.write_text("# 港口\n", encoding="utf-8")
        with patch("ui.left_panel.load_canon_entry", wraps=load_canon_entry) as parse:
            self.panel.set_project(self.project)
            self.assertEqual(parse.call_count, 1)
            self.assertEqual(parse.call_args.args[0], self.world)
        power = self.project.list_power()[0]
        item = self.item(power)
        badge = item.text(1)
        self.assertTrue(item.data(0,self.panel.CANON_STATUS_ROLE))
        self.panel.refresh_system_importance()
        self.assertEqual(item.text(1), badge)
        self.assertIn("模板分区", item.toolTip(0))

    def test_outside_notes_do_not_count_as_synchronized_state(self):
        from core.canon_schema import SECTION_START, SECTION_END
        source = ("# 白鸥\n\n" + SECTION_START + "\n## 当前剧情状态（DeepSonder 同步）\n\n"
            + MANAGED_STATE_START + "\n- 当前状态：未记录\n" + MANAGED_STATE_END + "\n"
            + SECTION_END + "\n\n- 作者备注：并未同步\n")
        self.card.write_text(source, encoding="utf-8")
        self.panel.set_project(self.project)
        self.assertFalse(self.item(self.card).data(0,self.panel.CANON_STATUS_ROLE)["synced"])


if __name__ == "__main__":
    import unittest
    unittest.main()
