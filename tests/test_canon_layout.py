"""Layout regressions use disposable canon documents, never user projects."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton

from tests.test_canon_schema import character_text
from ui.canon_layout import CanonModuleHeader
from ui.structured_canon_page import StructuredCanonPage
from ui.theme import apply_theme, colors_for


POWER_TEXT = """# 战力体系

- 筑基：可御剑飞行，神识外放。
- 金丹：可开宗立派，掌握领域雏形。
- 元婴：可元神出窍，影响一域。
- 化神：接近传说，天地异象。

## 限制

- 炼气期不能使用金丹期法术。
- 角色升级必须有奇遇、修炼或突破情节，不能凭空越级。
"""


class CanonLayoutTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc") if Path("C:/Windows/Fonts/msyh.ttc").exists() else -1

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "power.md"
        self.path.write_text(POWER_TEXT, encoding="utf-8")
        apply_theme(self.app, {"theme": "light"})
        self.page = StructuredCanonPage()
        self.page.open_path(self.path, "power")
        self.page.resize(1100, 800)
        self.page.show()
        self.settle()

    def tearDown(self):
        self.page.reset()
        self.page.close()
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.tmp.cleanup()

    def settle(self):
        QTest.qWait(40)
        self.app.processEvents()

    def editor(self):
        return self.page.form._text_widgets[0][1]

    def header(self):
        return self.page.findChild(QFrame, "canonModuleFrame").findChild(CanonModuleHeader)

    def test_short_cards_do_not_stretch_with_tall_window(self):
        form = self.page.form
        card = self.page.findChild(QFrame, "canonModuleFrame")
        before = card.height()
        self.page.resize(1100, 1200)
        self.settle()
        self.assertEqual(card.height(), before)
        self.assertLess(card.height(), 220)
        self.assertLess(form._preamble_widgets[0][1].height(), 180)
        self.assertEqual(self.editor().verticalScrollBar().maximum(), 0)
        self.assertFalse(form.is_modified())
        self.assertEqual(self.path.read_text(encoding="utf-8"), POWER_TEXT)

    def test_text_grows_shrinks_and_scrolls_at_its_limit(self):
        editor = self.editor()
        initial = editor.height()
        editor.setPlainText("\n".join("第%d条规则" % i for i in range(8)))
        self.settle()
        grown = editor.height()
        self.assertGreater(grown, initial)
        self.assertEqual(editor.verticalScrollBar().maximum(), 0)
        editor.setPlainText("\n".join("第%d条规则" % i for i in range(80)))
        self.settle()
        self.assertLess(editor.height(), editor.fontMetrics().lineSpacing() * 13)
        self.assertGreater(editor.verticalScrollBar().maximum(), 0)
        editor.setPlainText("一条规则。")
        self.settle()
        self.assertLess(editor.height(), grown)
        self.assertEqual(editor.verticalScrollBar().maximum(), 0)
        self.assertTrue(self.page.form.is_modified())
        self.assertEqual(self.path.read_text(encoding="utf-8"), POWER_TEXT)

    def test_wrapping_and_font_changes_refit_without_edits(self):
        self.path.write_text("# 战力体系\n\n## 限制\n\n" + "规则描述需要完整显示，" * 22 + "\n", encoding="utf-8")
        self.page.open_path(self.path, "power")
        self.settle()
        editor = self.editor()
        wide = editor.height()
        self.page.resize(520, 800)
        self.settle()
        narrow = editor.height()
        self.assertGreater(narrow, wide)
        apply_theme(self.app, {"theme": "dark", "ui_font_size": 20})
        self.settle()
        self.assertGreater(editor.height(), narrow)
        self.assertFalse(self.page.form.is_modified())
        self.assertEqual(self.page.form.form_scroll.horizontalScrollBar().maximum(), 0)

    def test_header_actions_reflow_without_overlap_in_both_themes(self):
        for theme in ("light", "dark"):
            for font_size in (14, 20):
                apply_theme(self.app, {"theme": theme, "ui_font_size": font_size})
                self.settle()
                for width in (420, 1100, 520):
                    with self.subTest(theme=theme, font=font_size, width=width):
                        self.page.resize(width, 800)
                        self.settle()
                        header = self.header()
                        grid = header.layout()
                        row, _, _, _ = grid.getItemPosition(grid.indexOf(header.operations))
                        # ⋯ 菜单取代了四个常驻按钮后，这些宽度不再需要换行堆叠；
                        # 标题确实过长的堆叠场景由 test_long_module_titles_* 覆盖。
                        self.assertEqual(row, 0)
                        self.assertFalse(header.heading.geometry().intersects(header.operations.geometry()))
                        self.assertTrue(header.rect().contains(header.operations.geometry()))
                        self.assertEqual(self.page.form.form_scroll.horizontalScrollBar().maximum(), 0)
                        self.assertFalse(self.page.form.is_modified())

    def test_page_title_stays_visible_above_actions_at_narrow_large_font(self):
        apply_theme(self.app, {"theme": "dark", "ui_font_size": 20})
        self.settle()
        self.page.resize(520, 720)
        self.settle()
        title = self.page.title_label
        self.assertGreaterEqual(title.width(), title.fontMetrics().horizontalAdvance(title.text()))
        toolbar = self.page.findChild(CanonModuleHeader, "canonPageToolbar")
        self.assertLess(title.geometry().bottom(), toolbar.operations.geometry().top())
        self.assertTrue(toolbar.rect().contains(toolbar.operations.geometry()))

    def test_long_module_titles_wrap_above_actions_without_clipping(self):
        heading = "长名称资料模块" * 18
        self.path.write_text("# 战力体系\n\n## " + heading + "\n\n一条规则。\n", encoding="utf-8")
        self.page.open_path(self.path, "power")
        for theme in ("light", "dark"):
            apply_theme(self.app, {"theme": theme, "ui_font_size": 20})
            self.settle()
            self.page.resize(520, 800)
            self.settle()
            header = self.header()
            title = next(label for label in header.heading.findChildren(QLabel) if label.text() == heading)
            self.assertGreaterEqual(title.height(), title.heightForWidth(title.width()))
            self.assertFalse(header.heading.geometry().intersects(header.operations.geometry()))
            self.assertEqual(self.page.form.form_scroll.horizontalScrollBar().maximum(), 0)

    def test_cards_have_opaque_theme_surfaces(self):
        for theme in ("light", "dark"):
            config = {"theme": theme}
            apply_theme(self.app, config)
            self.settle()
            card = self.page.findChild(QFrame, "canonModuleFrame")
            pixmap = card.grab()
            scale = pixmap.devicePixelRatio()
            pixel = pixmap.toImage().pixelColor(round((card.width() - 10) * scale), round((card.height() - 10) * scale))
            self.assertEqual(pixel.name().upper(), colors_for(config)["panel_color"])
            # 模块靠表面色区分层级，不再叠加投影，避免一屏多张卡片互相投阴影。
            self.assertIsNone(card.graphicsEffect())

    def test_group_rows_divider_instead_of_nested_card(self):
        self.path = Path(self.tmp.name) / "character.md"
        self.path.write_text(character_text(), encoding="utf-8")
        self.page.open_path(self.path, "character")
        self.settle()
        for theme in ("light", "dark"):
            config = {"theme": theme}
            apply_theme(self.app, config)
            self.settle()
            group = self.page.findChild(QFrame, "canonGroupFrame")
            self.assertIsNotNone(group)  # 角色卡的能力档案按分组条目呈现
            self.assertIsNone(group.graphicsEffect())
            card = group
            while card is not None and card.objectName() != "canonModuleFrame":
                card = card.parentWidget()
            self.assertIsNotNone(card)
            pixmap = card.grab()
            scale = pixmap.devicePixelRatio()
            point = group.mapTo(card, QPoint(group.width() // 2, group.height() - 4))
            pixel = pixmap.toImage().pixelColor(round(point.x() * scale), round(point.y() * scale))
            # 分组不再自带 __FIELD__ 卡片底，分隔线之下透出模块表面色。
            self.assertEqual(pixel.name().upper(), colors_for(config)["panel_color"])

    def test_collapse_keeps_draft_and_restores_editor_on_undo(self):
        form = self.page.form
        editor = self.editor()
        editor.setFocus()
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        editor.insertPlainText(" 新规则")
        self.settle()
        form.set_modules_expanded(False)
        self.settle()
        self.assertFalse(editor.isVisible())
        self.assertTrue(self.header().heading.isVisible())
        self.assertTrue(form.is_modified())
        form.edit_command("undo")
        self.settle()
        self.assertTrue(editor.isVisible())
        self.assertNotIn("新规则", editor.toPlainText())
        self.assertEqual(self.path.read_text(encoding="utf-8"), POWER_TEXT)
