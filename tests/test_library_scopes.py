import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, SkipTest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from core.project import NovelProject
from ui.left_panel import LeftPanel


class LibraryScopesTests(TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        if existing and not isinstance(existing, QApplication):
            raise SkipTest('Requires QApplication')
        cls.app = existing or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = NovelProject.create(Path(self.tmp.name) / 'project', '导航测试')
        for i in range(40):
            (self.project.chapters_dir / f'chapter_{i:02}.md').write_text(f'# 第{i}章\n\n正文', encoding='utf-8')
        self.panel = LeftPanel()
        self.addCleanup(self.panel.close)
        self.panel.resize(280, 650)
        self.panel.set_project(self.project)
        self.panel.show()
        self.app.processEvents()

    def visible_categories(self):
        return [self.panel.tree.topLevelItem(i).data(0, self.panel.CATEGORY_ROLE)
                for i in range(self.panel.tree.topLevelItemCount())
                if not self.panel.tree.topLevelItem(i).isHidden()]

    def test_default_chapters_and_separate_canon_controls(self):
        self.assertEqual(self.visible_categories(), ['章节'])
        self.assertTrue(self.panel.add_chapter_button.isVisible())
        self.assertFalse(self.panel.add_canon_button.isVisible())
        self.panel.scope_buttons['canon'].click()
        self.assertNotIn('章节', self.visible_categories())
        self.assertIn('角色', self.visible_categories())
        self.assertTrue(self.panel.add_canon_button.isVisible())
        self.assertFalse(self.panel.add_chapter_button.isVisible())

    def test_search_and_scroll_survive_scope_switch_and_refresh(self):
        self.panel.search.setText('第')
        self.panel.tree.doItemsLayout()
        bar = self.panel.tree.verticalScrollBar()
        bar.setValue(bar.maximum())
        previous = bar.value()
        self.assertGreater(previous, 0)
        self.panel.set_scope('canon')
        self.assertEqual(self.panel.search.text(), '')
        self.panel.search.setText('没有此资料')
        self.assertTrue(self.panel.empty_label.isVisible())
        self.panel.set_scope('chapters')
        self.assertEqual(self.panel.search.text(), '第')
        self.assertEqual(bar.value(), previous)
        self.panel.set_project(self.project)
        self.assertEqual(bar.value(), previous)
        self.panel.set_scope('canon')
        self.assertEqual(self.panel.search.text(), '没有此资料')

    def test_explicit_reveal_switches_scope_and_clears_filter_without_opening(self):
        self.panel.search.setText('不存在')
        opened = []
        self.panel.file_selected.connect(lambda *args: opened.append(args))
        target = self.project.outline_dir / 'story_plan.json'
        self.assertTrue(self.panel.reveal_path(target))
        self.assertEqual(self.panel._scope, 'canon')
        self.assertEqual(self.panel.search.text(), '')
        self.assertEqual(opened, [])
        self.assertEqual(self.panel.tree.currentItem().data(0, self.panel.PATH_ROLE), str(target))
        self.panel.select_path(self.project.chapters_dir / 'chapter_01.md')
        self.assertEqual(self.panel._scope, 'chapters')
        self.assertEqual(len(opened), 1)

    def test_keyboard_activation_opens_selected_document(self):
        target = self.project.chapters_dir / 'chapter_01.md'
        self.panel.reveal_path(target)
        opened = []
        self.panel.file_selected.connect(lambda *args: opened.append(args))
        self.panel.tree.setFocus()
        QTest.keyClick(self.panel.tree, Qt.Key.Key_Return)
        self.assertEqual(opened, [('章节', str(target))])

    def test_project_switch_clears_old_search_and_selection(self):
        self.panel.search.setText('第')
        other = NovelProject.create(Path(self.tmp.name) / 'other', '另一本书')
        self.panel.set_project(other)
        self.assertEqual(self.panel.search.text(), '')
        self.assertIsNone(self.panel._selected_path)

    def test_flat_documents_and_fixed_style_shortcut(self):
        self.panel.set_scope('canon')
        docs = list(self.panel.document_items())
        for path in (self.project.outline_dir / 'story_plan.json',):
            item = next(item for item in docs if item.data(0, self.panel.PATH_ROLE) == str(path))
            self.assertIsNone(item.parent())
            self.panel.select_path(path)
            self.assertIs(self.panel.tree.currentItem(), item)
        self.assertFalse(any(item.data(0, self.panel.PATH_ROLE) == str(self.project.style_guide_path) for item in docs))
        called = []
        self.panel.style_requested.connect(lambda: called.append(True))
        self.panel.search.setText('没有匹配')
        self.assertTrue(self.panel.style_button.isVisible())
        self.panel.style_button.click()
        self.assertEqual(called, [True])
        self.panel.set_style_available(False)
        self.panel.set_project(self.project)
        self.assertFalse(self.panel.style_button.isEnabled())
        self.panel.set_scope('chapters')
        self.assertFalse(self.panel.style_button.isVisible())
        self.panel.set_project(None)
        self.assertFalse(self.panel.style_button.isEnabled())

    def test_flat_document_search_and_scope_aware_locate(self):
        path = self.project.outline_dir / 'story_plan.json'
        self.panel.set_current_document(path)
        self.assertFalse(self.panel.locate_button.isEnabled())
        self.panel.set_scope('canon')
        self.assertTrue(self.panel.locate_button.isEnabled())
        self.panel.search.setText('时间线')
        self.assertEqual(self.visible_categories(), ['时间线'])
        self.assertFalse(self.panel.empty_label.isVisible())
        self.panel.set_current_document(None)
        self.assertFalse(self.panel.locate_button.isEnabled())

    def test_group_click_keyboard_and_refresh_preserve_collapse(self):
        self.panel.set_scope('canon')
        group = next(self.panel.tree.topLevelItem(i) for i in range(self.panel.tree.topLevelItemCount())
                     if self.panel.tree.topLevelItem(i).data(0, self.panel.CATEGORY_ROLE) == '体系设定')
        self.panel.select_path(Path(group.child(0).data(0, self.panel.PATH_ROLE)))
        self.panel.tree.setAnimated(False)
        self.app.processEvents()
        self.panel.tree.scrollToItem(group)
        self.app.processEvents()
        rect = self.panel.tree.visualItemRect(group)
        QTest.mouseClick(self.panel.tree.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(rect.left() + 60, rect.center().y()))
        self.assertFalse(group.isExpanded())
        self.panel.set_project(self.project)
        group = next(self.panel.tree.topLevelItem(i) for i in range(self.panel.tree.topLevelItemCount())
                     if self.panel.tree.topLevelItem(i).data(0, self.panel.CATEGORY_ROLE) == '体系设定')
        self.assertFalse(group.isExpanded())
        self.panel.tree.setCurrentItem(group)
        QTest.keyClick(self.panel.tree, Qt.Key.Key_Return)
        self.assertTrue(group.isExpanded())

    def test_system_badges_survive_narrow_width_and_refresh(self):
        from core.project_data import ProjectDataStore
        store = ProjectDataStore(self.project)
        self.panel.set_scope('canon')
        group = next(self.panel.tree.topLevelItem(i) for i in range(self.panel.tree.topLevelItemCount())
                     if self.panel.tree.topLevelItem(i).data(0, self.panel.CATEGORY_ROLE) == '体系设定')
        self.assertNotIn('核心 0', group.text(0))
        always = next(group.child(i) for i in range(group.childCount()) if group.child(i).data(0, self.panel.IMPORTANCE_ROLE) == 'always')
        regular = next(group.child(i) for i in range(group.childCount()) if group.child(i).data(0, self.panel.IMPORTANCE_ROLE) == 'non_core')
        self.assertIn('常驻', always.text(0))
        self.assertEqual(regular.text(1), '')
        store.set_system_importance(Path(regular.data(0, self.panel.PATH_ROLE)), "core")
        self.panel.refresh_system_importance()
        self.assertIn("核心", regular.text(0))
        self.panel.resize(450, 650); self.app.processEvents()
        self.assertNotIn('常驻', always.text(0))
        self.assertEqual(always.text(1), '常驻')
        self.panel.refresh_system_importance()
        self.assertNotIn('核心 0', group.text(0))
        self.panel.resize(280, 650); self.app.processEvents()
        self.assertIn('常驻', always.text(0))

    def test_narrow_themes_large_font_controls_fit(self):
        from ui.theme import apply_theme
        from PySide6.QtGui import QFontDatabase, QFont
        font = Path('C:/Windows/Fonts/NotoSansSC-VF.ttf')
        if font.exists():
            families = QFontDatabase.applicationFontFamilies(QFontDatabase.addApplicationFont(str(font)))
            if families: self.app.setFont(QFont(families[0]))
        for theme in ('light', 'dark'):
            apply_theme(self.app, {'theme': theme, 'ui_font_size': 18})
            self.panel.set_theme({'theme': theme})
            self.panel.resize(280, 650); self.panel.set_scope('canon'); self.app.processEvents()
            self.assertLessEqual(self.panel.minimumSizeHint().width(), 280)
            self.assertLessEqual(abs(self.panel.scope_buttons['canon'].width() - self.panel.scope_buttons['chapters'].width()), 1)
            self.assertTrue(self.panel.rect().contains(self.panel.style_footer.geometry()))
            self.assertTrue(self.panel.style_button.isVisible())
            self.assertGreater(self.panel.search.width(), 70)
            self.assertEqual(self.panel.tree.horizontalScrollBar().maximum(), 0)
