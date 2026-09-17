import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, SkipTest
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, Qt
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
        target = self.project.outline_dir / 'main_arc.md'
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
