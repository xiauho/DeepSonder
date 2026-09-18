import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, SkipTest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QApplication,QDialog,QPushButton
from core.project import NovelProject
from core.config import DEFAULT_CONFIG
from core.timeline_events import TimelineStore
from ui.timeline_page import TimelinePage,EventToChapterDialog
from ui.main_window import MainWindow


class TimelinePageTests(TestCase):
    @classmethod
    def setUpClass(cls):
        existing=QCoreApplication.instance()
        if existing and not isinstance(existing,QApplication): raise SkipTest('Requires QApplication')
        cls.app=existing or QApplication([])

    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.project=NovelProject.create(self.root/'project','时间轴')
        self.store=TimelineStore(self.project)
        self.first=self.store.save_event({'title':'港口来信','time':'雨夜','description':'收到密信','major':True,'chapter_ids':['chapter_01'],'storyline':'主线'})
        self.second=self.store.save_event({'title':'灯塔失火','time':'三天后','storyline':'支线'})
        self.third=self.store.save_event({'title':'未采用的离港计划','state':'abandoned'})
        self.page=TimelinePage();self.page.show_project(self.project);self.page.resize(1000,720);self.page.show()
        self.addCleanup(self.page.close);self.app.processEvents()

    def test_all_active_events_include_abandoned_and_unknown_time(self):
        self.assertEqual(self.page.model.rowCount(),3)
        self.assertIn('时间待定',self.page.model.index(2).data())
        self.assertIn('已放弃',self.page.model.index(2).data())
        self.assertEqual([e['id'] for e in self.page.model.events],[self.first,self.second,self.third])

    def test_filter_blocks_reordering_and_refresh_preserves_selection(self):
        self.page.list.setCurrentIndex(self.page.model.index(1))
        self.page.search.setText('灯塔')
        self.assertEqual(self.page.current()['id'],self.second)
        self.assertFalse(self.page.move_button.isEnabled())
        self.page.move(-1)
        self.assertEqual(TimelineStore(self.project).events()[0]['id'],self.first)
        other=TimelineStore(self.project)
        event=other.events()[1];event['description']='新的细节'
        other.save_event(event,event['id'])
        self.page.reload()
        self.assertEqual(self.page.search.text(),'灯塔')
        self.assertIn('新的细节',self.page.detail.toPlainText())

    def test_delete_restore_and_move_keep_times_and_material(self):
        self.store.save_selection('chapter_01',{self.first:'develop'})
        self.store.move_to(self.second,self.first)
        self.assertEqual(self.store.events()[0]['id'],self.second)
        self.assertEqual(self.store.events()[0]['time'],'三天后')
        self.assertEqual(self.store.selection('chapter_01'),{self.first:'develop'})
        self.page.reload();self.page.list.setCurrentIndex(self.page.model.index(0))
        self.page.toggle_deleted()
        self.assertEqual(self.page.model.rowCount(),2)
        self.page.deleted_filter.setChecked(True)
        self.assertEqual(self.page.current()['id'],self.second)
        self.page.toggle_deleted()
        self.page.deleted_filter.setChecked(False)
        self.assertEqual(self.page.model.rowCount(),3)

    def test_material_requires_explicit_chapter_and_valid_use(self):
        before=self.store.path.read_bytes()
        dialog=EventToChapterDialog(self.store,self.store.events()[0])
        self.addCleanup(dialog.close)
        dialog.accept()
        self.assertIn('请选择',dialog.error.text())
        self.assertEqual(self.store.path.read_bytes(),before)
        dialog.chapter.setCurrentIndex(0)
        dialog.use.setCurrentIndex(dialog.use.findData('background'))
        dialog.accept()
        self.assertIn('背景事实',dialog.error.text())
        dialog.use.setCurrentIndex(dialog.use.findData('develop'))
        dialog.accept()
        self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
        self.assertEqual(TimelineStore(self.project).selection('chapter_01'),{self.first:'develop'})
        self.assertEqual(dialog.chapter_id,'chapter_01')

    def test_narrow_detail_back_retains_filter_and_selection(self):
        self.page.resize(580,680);self.app.processEvents()
        self.page.search.setText('灯塔')
        self.page.open_detail();self.app.processEvents()
        self.assertFalse(self.page.overview.isVisible())
        self.assertTrue(self.page.back_button.isVisible())
        self.page.close_detail()
        self.assertTrue(self.page.overview.isVisible())
        self.assertEqual(self.page.current()['id'],self.second)
        self.assertEqual(self.page.search.text(),'灯塔')

    def test_large_timeline_does_not_create_widgets_per_event(self):
        data=self.store.data.copy()
        template=data['events'][0]
        data['events']=[dict(template,id=f'event-{i}',title=f'事件 {i}') for i in range(1500)]
        self.store._commit(data)
        self.page.reload();self.app.processEvents()
        self.assertEqual(self.page.model.rowCount(),1500)
        self.assertLess(len(self.page.findChildren(QPushButton)),25)
        self.page.list.setCurrentIndex(self.page.model.index(1200))
        self.page.list.scrollTo(self.page.list.currentIndex());self.app.processEvents()
        position=self.page.list.verticalScrollBar().value()
        self.page.reload();self.app.processEvents()
        self.assertEqual(self.page.current()['id'],'event-1200')
        self.assertEqual(self.page.list.verticalScrollBar().value(),position)

    def test_deleted_storyline_filter_restores_on_another_page(self):
        self.store.set_deleted(self.second, True)
        self.page.deleted_filter.setChecked(True)
        self.page.story_filter.setCurrentIndex(self.page.story_filter.findData('支线'))
        state = self.page.view_state()
        other = TimelinePage()
        self.addCleanup(other.close)
        other.show_project(self.project)
        other.restore_state(state)
        self.assertTrue(other.deleted_filter.isChecked())
        self.assertEqual(other.story_filter.currentData(), '支线')
        self.assertEqual(other.current()['id'], self.second)

    def test_corrupt_data_is_not_replaced_by_empty_timeline(self):
        self.store.path.write_text('{broken',encoding='utf-8')
        self.page.reload()
        self.assertIn('格式无效',self.page.message.text())
        self.assertFalse(self.page.new_button.isEnabled())
        self.assertEqual(self.store.path.read_text(encoding='utf-8'),'{broken')


class TimelineWindowTests(TestCase):
    @classmethod
    def setUpClass(cls):
        existing=QCoreApplication.instance()
        if existing and not isinstance(existing,QApplication): raise SkipTest('Requires QApplication')
        cls.app=existing or QApplication([])

    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.project=NovelProject.create(self.root/'project','时间轴集成')
        store=TimelineStore(self.project)
        self.event_id=store.save_event({'title':'密信抵达','chapter_ids':['chapter_01']})
        patcher=patch.object(MainWindow,'_restore_last_project');patcher.start();self.addCleanup(patcher.stop)
        self.window=MainWindow(config={**DEFAULT_CONFIG,'auto_save':False},ui_state_path=self.root/'ui.json')
        self.window.project_lifecycle_controller.persist_config=lambda _:None
        self.addCleanup(self.window.close)
        self.assertTrue(self.window._load_project(self.project.root,quiet=True))
        self.window.show();self.events()

    def events(self):
        for _ in range(3): self.app.processEvents()

    def test_virtual_entry_and_hidden_document_actions(self):
        w=self.window
        (self.project.canon_dir/'timeline.md').unlink()
        w.left_panel.set_project(self.project);w.left_panel.select_timeline()
        item=w.left_panel.tree.currentItem()
        self.assertIsNone(item.data(0,w.left_panel.PATH_ROLE))
        w.left_panel._on_item_clicked(item,0);self.events()
        self.assertIs(w.content_stack.currentWidget(),w.timeline_page)
        for key in ('save','undo','redo','find','delete_chapter','expand','continuation','check','memory','focus'):
            self.assertFalse(w.actions[key].isEnabled(),key)
        with patch.object(w.editor,'undo') as undo,patch.object(w.document_controller,'save') as save:
            w.actions['undo'].trigger();w.actions['save'].trigger()
            undo.assert_not_called();save.assert_not_called()
        w.toggle_focus_mode()
        self.assertFalse(w.window_state_controller.focus_mode)

    def test_failed_save_keeps_document_and_selection(self):
        w=self.window
        path=w.editor.current_path()
        w.left_panel.select_timeline()
        with patch.object(w.editor,'is_dirty',return_value=True),patch.object(w,'_save_if_dirty',return_value=False):
            self.assertFalse(w.manage_timeline())
        self.assertIs(w.content_stack.currentWidget(),w.editor)
        self.assertEqual(w.editor.current_path(),path)
        self.assertEqual(w.left_panel.tree.currentItem().data(0,w.left_panel.PATH_ROLE),path)

    def test_jump_back_and_notes_do_not_overwrite_documents(self):
        w=self.window
        path=self.project.chapters_dir/'chapter_01.md'
        original=path.read_bytes()
        notes=self.project.canon_dir/'timeline.md';old_notes=notes.read_bytes()
        w.manage_timeline();w.timeline_page.search.setText('密信')
        w._open_timeline_chapter('chapter_01');self.events()
        self.assertIs(w.content_stack.currentWidget(),w.editor)
        self.assertTrue(w.actions['save'].isEnabled())
        w.manage_timeline()
        self.assertEqual(w.timeline_page.search.text(),'密信')
        self.assertEqual(w.timeline_page.current()['id'],self.event_id)
        w._open_timeline_notes();self.events()
        self.assertEqual(Path(w.editor.current_path()),notes)
        self.assertEqual(notes.read_bytes(),old_notes)
        self.assertEqual(path.read_bytes(),original)

    def test_switch_project_clears_timeline_state(self):
        w=self.window
        w.manage_timeline();w.timeline_page.search.setText('密信')
        other=NovelProject.create(self.root/'other','另一个项目')
        self.assertTrue(w._load_project(other.root,quiet=True))
        w.manage_timeline();self.events()
        self.assertEqual(w.timeline_page.model.rowCount(),0)
        self.assertEqual(w.timeline_page.search.text(),'')
        self.assertEqual(w.timeline_page.detail.toPlainText(),'选择一个事件查看详情。')
        self.assertTrue(w._load_project(self.project.root,quiet=True))
        self.assertTrue(w._timeline_active())
        self.assertEqual(w.timeline_page.search.text(),'密信')
