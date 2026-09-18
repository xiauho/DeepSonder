import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, SkipTest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtWidgets import QApplication, QDialog
from core.project import NovelProject
from core.timeline_events import TimelineStore
from ui.timeline_dialog import EventEditorDialog, EventMaterialDialog
from ui.left_panel import LeftPanel
from ui.timeline_page import TimelinePage
from ui.theme import apply_theme


class TimelineUITests(TestCase):
    @classmethod
    def setUpClass(cls):
        existing = QCoreApplication.instance()
        if existing and not isinstance(existing, QApplication):
            raise SkipTest('Requires QApplication')
        cls.app = existing or QApplication([])
        font_path = Path('C:/Windows/Fonts/msyh.ttc')
        if font_path.exists():
            font_id = QFontDatabase.addApplicationFont(str(font_path))
            families = QFontDatabase.applicationFontFamilies(font_id)
            if families:
                cls.app.setFont(QFont(families[0]))

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = NovelProject.create(Path(self.tmp.name)/'project','时间线测试')
        self.store = TimelineStore(self.project)
        self.first = self.store.save_event({'title':'密信抵达','description':'林砚在雾港收到匿名密信。','time':'雨夜','characters':'林砚','storyline':'主线','major':True,'chapter_ids':['chapter_01']})
        self.second = self.store.save_event({'title':'前往灯塔','description':'林砚循着线索离开港口。'})
        self.addCleanup(lambda: apply_theme(self.app, {'theme':'light'}))

    def test_editor_validation_preserves_input_and_saves_chapter_link(self):
        dialog = EventEditorDialog(self.store)
        self.addCleanup(dialog.close)
        dialog.accept()
        self.assertIn('标题',dialog.error.text())
        dialog.fields['title'].setText('发现灯塔暗室')
        dialog.state.setCurrentIndex(dialog.state.findData('written'))
        dialog.accept()
        self.assertIn('至少',dialog.error.text())
        self.assertEqual(dialog.fields['title'].text(),'发现灯塔暗室')
        dialog.chapters.item(0).setCheckState(Qt.CheckState.Checked)
        dialog.accept()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
        event = next(x for x in TimelineStore(self.project).events() if x['id']==dialog.saved_id)
        self.assertEqual(event['chapter_ids'],['chapter_01'])

    def test_search_filters_delete_restore_and_reorder(self):
        dialog = TimelinePage()
        dialog.show_project(self.project)
        self.addCleanup(dialog.close)
        dialog.search.setText('林砚')
        self.assertEqual(dialog.model.rowCount(),2)
        dialog.major_filter.setChecked(True)
        self.assertEqual(dialog.model.rowCount(),1)
        self.assertFalse(dialog.down_button.isEnabled())
        dialog.search.clear()
        dialog.major_filter.setChecked(False)
        dialog.list.setCurrentIndex(dialog.model.index(1))
        dialog.move(-1)
        self.assertEqual(dialog.current()['id'],self.second)
        dialog.toggle_deleted()
        self.assertEqual(dialog.model.rowCount(),1)
        dialog.deleted_filter.setChecked(True)
        self.assertEqual(dialog.current()['id'],self.second)
        self.assertEqual(dialog.delete_button.text(),'恢复')
        dialog.toggle_deleted()
        self.assertEqual(len(TimelineStore(self.project).events()),2)

    def test_picker_remembers_explicit_use_and_filter_does_not_clear(self):
        dialog = EventMaterialDialog(self.project,'chapter_01')
        self.addCleanup(dialog.close)
        event,use,panel = dialog.rows[0]
        self.assertFalse(use.model().item(use.findData('background')).isEnabled())
        use.setCurrentIndex(use.findData('develop'))
        dialog.search.setText('找不到的事件')
        self.assertIn('已选 1',dialog.summary.toPlainText())
        dialog.accept()
        self.assertEqual(TimelineStore(self.project).selection('chapter_01'),{self.first:'develop'})
        reopened = EventMaterialDialog(self.project,'chapter_01')
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.rows[0][1].currentData(),'develop')
        reopened.rows[0][1].setCurrentIndex(0)
        reopened.reject()
        self.assertEqual(TimelineStore(self.project).selection('chapter_01'),{self.first:'develop'})

    def test_picker_detects_external_changes(self):
        dialog = EventMaterialDialog(self.project,'chapter_01')
        self.addCleanup(dialog.close)
        TimelineStore(self.project).set_deleted(self.first,True)
        dialog.accept()
        self.assertIn('其他窗口',dialog.error.text())
        self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected)

    def test_navigation_entry_enabled_only_with_project(self):
        panel=LeftPanel()
        self.addCleanup(panel.close)
        self.assertFalse(any(panel.tree.topLevelItem(i).data(0,panel.FEATURE_ROLE) for i in range(panel.tree.topLevelItemCount())))
        panel.set_project(self.project)
        hits=[]
        panel.timeline_requested.connect(lambda: hits.append(True))
        panel.select_timeline()
        panel._on_item_clicked(panel.tree.currentItem(),0)
        self.assertEqual(hits,[True])
        panel.set_project(None)
        self.assertEqual(panel.tree.topLevelItemCount(),0)

    def test_narrow_layout_in_both_themes_and_large_font(self):
        for theme in ('light','dark'):
            for size in (14,20):
                apply_theme(self.app,{'theme':theme,'ui_font_size':size})
                for factory in (lambda: TimelinePage(),lambda: EventMaterialDialog(self.project,'chapter_01'),lambda: EventEditorDialog(self.store)):
                    dialog=factory()
                    dialog.resize(580,680)
                    dialog.show()
                    self.app.processEvents()
                    self.assertLessEqual(dialog.width(),580)
                    self.assertLessEqual(dialog.height(),680)
                    for button in dialog.findChildren(__import__('PySide6.QtWidgets',fromlist=['QPushButton']).QPushButton):
                        if button.isVisible():
                            self.assertGreater(button.width(),0)
                    dialog.close()
