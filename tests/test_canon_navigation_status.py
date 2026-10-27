import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QHoverEvent, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem
from core.canon_schema import load_canon_entry
from core.character_card_sync import MANAGED_STATE_START, MANAGED_STATE_END
from core.project import NovelProject
from tests.test_canon_schema import character_text
from ui.left_panel import LeftPanel
from ui.theme import apply_theme, colors_for


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
        # 窄侧栏：状态列保留为着色圆点，名称列只承载名称，文字细节在提示里。
        self.assertFalse(self.panel.tree.isColumnHidden(1))
        self.assertEqual(self.panel.tree.columnWidth(1), self.panel.COMPACT_STATUS_WIDTH)
        self.assertEqual(card.text(0), "白鸥")
        self.assertEqual(card.text(1), self.panel.STATUS_DOT)
        self.assertEqual(world.text(1), self.panel.STATUS_DOT)
        self.assertIn("基础档案：已填写", card.toolTip(0))
        self.assertIn("资料内容：尚缺6个模板分区", world.toolTip(1))
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
        self.assertEqual(self.item(self.card).text(1), self.panel.STATUS_DOT)
        self.assertIn("基础档案：尚缺5项预设字段", self.item(self.card).toolTip(1))
        self.assertIn("待同步", self.item(self.card).toolTip(0))
        self.assertEqual(self.panel.search.text(), "白鸥")
        self.assertEqual(self.panel.tree.currentItem().data(0, self.panel.PATH_ROLE), str(self.card))
        self.assertEqual(self.card.read_bytes(), before)

    def test_ambiguous_and_invalid_documents_show_actionable_status_without_crash(self):
        self.card.write_text("# 白鸥\n\n## 基础档案\n- 姓名：甲\n- 姓名：乙\n", encoding="utf-8")
        self.world.write_bytes(b"\xffbroken")
        self.panel.set_project(self.project)
        card = self.item(self.card)
        world = self.item(self.world)
        self.assertEqual(card.text(1), self.panel.STATUS_DOT)
        self.assertIn("结构歧义", card.toolTip(1))
        self.assertEqual(world.text(1), self.panel.STATUS_DOT)
        self.assertIn("资料无法读取", world.toolTip(1))
        self.assertEqual(self.world.read_bytes(), b"\xffbroken")

    def test_compact_status_dot_carries_color_and_opens_status_menu(self):
        """窄侧栏圆点：颜色沿用强调/静默规则，且点它仍打开统一的资料状态浮层。"""
        card = self.item(self.card)
        self.assertEqual(card.foreground(1).color().name().upper(), self.panel._muted_color.upper())
        self.card.write_text("# 白鸥\n\n## 基础档案\n- 姓名：甲\n- 姓名：乙\n", encoding="utf-8")
        self.panel.set_project(self.project)
        self.assertEqual(self.item(self.card).foreground(1).color().name().upper(),
                         self.panel._accent_color.upper())
        with patch("ui.left_panel.QMenu") as menu:
            instance = menu.return_value
            self.panel._on_item_clicked(self.item(self.card), 1)
            labels = [call.args[0] for call in instance.addAction.call_args_list if call.args]
        self.assertTrue(instance.exec.called)
        self.assertIn("移入回收站", labels)

    def test_status_dot_column_keeps_panel_backdrop_when_row_is_selected_or_hovered(self):
        """选中或悬停只作用于名称卡片，右侧圆点列始终是资料栏背景色。"""
        for theme in ("light", "dark"):
            config = {"theme": theme}
            apply_theme(self.app, config)
            self.app.processEvents()
            card = self.item(self.card)
            world = self.item(self.world)

            def status_pixel(item):
                rect = self.panel.tree.visualRect(self.panel.tree.indexFromItem(item, 1))
                image = self.panel.tree.grab().toImage()
                scale = image.devicePixelRatio()
                point = self.panel.tree.viewport().mapTo(
                    self.panel.tree, QPoint(rect.left() + 2, rect.center().y()))
                return image.pixelColor(round(point.x() * scale), round(point.y() * scale))

            self.panel.tree.setCurrentItem(card)
            self.panel.tree.setFocus()
            self.app.processEvents()
            self.assertEqual(status_pixel(card).name().upper(),
                             colors_for(config)["background_color"].upper())

            # 选中另一行后悬停名称卡片：悬停底色只出现在名称列，圆点列仍是资料栏背景。
            self.panel.tree.setCurrentItem(world)
            target = self.panel.tree.visualRect(
                self.panel.tree.indexFromItem(card, 1)).center()
            viewport = self.panel.tree.viewport()
            self.app.sendEvent(viewport, QHoverEvent(
                QEvent.Type.HoverEnter, QPointF(target), QPointF(target), QPointF(target)))
            self.app.sendEvent(viewport, QHoverEvent(
                QEvent.Type.HoverMove, QPointF(target), QPointF(target), QPointF(target)))
            self.app.processEvents()
            name_rect = self.panel.tree.visualRect(self.panel.tree.indexFromItem(card, 0))
            name_point = self.panel.tree.viewport().mapTo(
                self.panel.tree, QPoint(name_rect.left() + 5, name_rect.center().y()))
            name_pixel = self.panel.tree.grab().toImage().pixelColor(name_point)
            self.assertEqual(name_pixel.name().upper(), colors_for(config)["hover_color"].upper())
            self.assertEqual(status_pixel(card).name().upper(),
                             colors_for(config)["background_color"].upper())

    def test_status_dot_hover_focus_and_text_alternatives(self):
        card = self.item(self.card)
        index = self.panel.tree.indexFromItem(card, 1)
        rect = self.panel.tree.visualRect(index)
        viewport = self.panel.tree.viewport()
        self.panel.search.setFocus()
        self.panel.tree.setCurrentItem(card)
        baseline = viewport.grab(rect).toImage()
        point = QPointF(rect.center())
        self.app.sendEvent(viewport, QHoverEvent(QEvent.Type.HoverMove, point, point, point))
        self.app.processEvents()
        self.assertEqual(self.panel.tree._status_hover, index)
        self.assertEqual(viewport.cursor().shape(), Qt.CursorShape.PointingHandCursor)
        self.assertNotEqual(viewport.grab(rect).toImage(), baseline)
        self.app.sendEvent(viewport, QEvent(QEvent.Type.Leave))
        self.panel.tree.setFocus()
        self.app.processEvents()
        self.assertNotEqual(viewport.grab(rect).toImage(), baseline)
        self.assertIn("白鸥", card.data(1, Qt.ItemDataRole.AccessibleTextRole))
        self.assertIn("Alt+Enter", card.data(1, Qt.ItemDataRole.AccessibleDescriptionRole))

    def test_status_dot_and_ring_share_pixel_center_across_fonts_and_scales(self):
        card = self.item(self.card)
        tree = self.panel.tree
        index = tree.indexFromItem(card, 1)
        delegate = tree.itemDelegateForColumn(1)

        def center_for(image, color):
            reference = color.getRgb()[:3]
            points = [(x, y) for y in range(image.height()) for x in range(image.width())
                      if sum((a - b) ** 2 for a, b in zip(image.pixelColor(x, y).getRgb()[:3], reference)) < 30 ** 2]
            self.assertTrue(points, color.name())
            xs, ys = zip(*points)
            return ((min(xs) + max(xs) + 1) / 2, (min(ys) + max(ys) + 1) / 2)

        for theme in ("light", "dark"):
            config = {"theme": theme}
            apply_theme(self.app, config)
            self.panel.set_theme(config)
            for family in ("Microsoft YaHei UI", "Segoe UI"):
                for size in (14, 20):
                    font = QFont(family)
                    font.setPixelSize(size)
                    card.setFont(1, font)
                    for scale in (1, 1.25, 1.5, 2):
                        for focused in (False, True):
                            with self.subTest(theme=theme, family=family, size=size, scale=scale, focused=focused):
                                self.panel.search.setFocus()
                                tree._status_hover = index
                                if focused:
                                    tree.setCurrentItem(card)
                                    tree.setFocus()
                                opt = QStyleOptionViewItem()
                                opt.initFrom(tree)
                                opt.rect = QRect(0, 0, 28, 44)
                                opt.font = font
                                opt.fontMetrics = QFontMetrics(font)
                                opt.state = QStyle.StateFlag.State_Enabled
                                image = QImage(round(28 * scale), round(44 * scale), QImage.Format.Format_ARGB32_Premultiplied)
                                image.setDevicePixelRatio(scale)
                                image.fill(QColor(colors_for(config)["background_color"]))
                                painter = QPainter(image)
                                delegate.paint(painter, opt, index)
                                painter.end()
                                dot = center_for(image, card.foreground(1).color())
                                ring = center_for(image, QColor(self.panel._accent_color))
                                self.assertLessEqual(abs(dot[0] - ring[0]), 1)
                                self.assertLessEqual(abs(dot[1] - ring[1]), 1)

    def test_alt_enter_opens_status_without_opening_or_writing_document(self):
        before = self.card.read_bytes()
        selected = []
        self.panel.file_selected.connect(lambda *args: selected.append(args))
        for width in (280, 580):
            self.panel.resize(width, 680)
            self.app.processEvents()
            item = self.item(self.card)
            self.panel.tree.setCurrentItem(item)
            self.panel.tree.setFocus()
            with patch.object(self.panel, "_show_status_menu") as menu:
                QTest.keyClick(self.panel.tree, Qt.Key.Key_Return, Qt.KeyboardModifier.AltModifier)
                self.assertIs(menu.call_args.args[0], item)
            self.assertEqual(selected, [])
            self.assertEqual(self.card.read_bytes(), before)

    def test_non_core_system_keeps_a_compact_status_target(self):
        item = next(i for i in self.panel.document_items() if i.data(0, self.panel.IMPORTANCE_ROLE))
        item.setData(0, self.panel.IMPORTANCE_ROLE, "non_core")
        self.panel._apply_document_appearance(item)
        self.assertEqual(item.text(1), self.panel.STATUS_DOT)
        self.assertIn("非核心", item.data(1, Qt.ItemDataRole.AccessibleTextRole))

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

    def test_row_click_still_opens_document(self):
        self.panel.resize(580, 680); self.app.processEvents()
        opened = []
        self.panel.file_selected.connect(lambda category, path: opened.append((category, path)))
        self.panel._on_item_clicked(self.item(self.card), 0)
        self.assertEqual(opened, [("角色", str(self.card))])

    def test_status_column_opens_status_menu_for_every_canon_kind(self):
        self.panel.resize(580, 680); self.app.processEvents()
        for path, expected in ((self.card, "基础档案：已填写"), (self.world, "模板分区")):
            with patch("ui.left_panel.QMenu") as menu:
                instance = menu.return_value
                self.panel._on_item_clicked(self.item(path), 1)
                labels = [call.args[0] for call in instance.addAction.call_args_list if call.args]
            self.assertTrue(instance.exec.called)
            self.assertIn("移入回收站", labels)
            self.assertTrue(any(expected in label for label in labels), labels)

    def test_status_menu_keeps_system_loading_policy(self):
        self.panel.resize(580, 680); self.app.processEvents()
        target = next(item for item in map(self.item, self.project.list_power())
                      if item.data(0, self.panel.IMPORTANCE_ROLE) != "always")
        with patch("ui.left_panel.QMenu") as menu:
            instance = menu.return_value
            self.panel._on_item_clicked(target, 1)
            labels = [call.args[0] for call in instance.addAction.call_args_list if call.args]
        self.assertIn("AI 加载策略", labels)
        self.assertIn("核心 · 自动作为重点上下文加载", labels)
        self.assertIn("非核心 · 扩写时手动选择", labels)
        self.assertIn("移入回收站", labels)

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
