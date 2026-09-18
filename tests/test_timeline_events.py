import json
import tempfile
from pathlib import Path
from unittest import TestCase
from core.project import NovelProject
from core.timeline_events import TimelineStore
from core.prompt_builder import build_expansion_prompt, build_write_prompt, build_check_prompt
from core.context_budget import Section, allocate
from core.task_context import AIContextSnapshot


class TimelineTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = NovelProject.create(Path(self.tmp.name) / 'project', '事件测试')
        (self.project.chapters_dir / 'chapter_02.md').write_text('# 第二章\n\n正文', encoding='utf-8')
        self.store = TimelineStore(self.project)

    def create(self, **kwargs):
        return self.store.save_event(dict(title='密信抵达', description='主角在港口收到密信。', **kwargs))

    def test_create_edit_restore_order_and_notes_preserved(self):
        notes = self.project.canon_dir / 'timeline.md'
        before = notes.read_bytes()
        first = self.create(time='三天后', major=True, chapter_ids=['chapter_01'])
        second = self.store.save_event({'title':'前往灯塔'})
        self.store.move(second, -1)
        self.assertEqual([e['id'] for e in TimelineStore(self.project).events()], [second,first])
        values = self.store.events()[1]
        values['title'] = '匿名密信抵达'
        self.store.save_event(values, first)
        self.store.set_deleted(first, True)
        self.assertEqual(len(self.store.events()), 1)
        self.store.set_deleted(first, False)
        self.assertEqual(self.store.events()[1]['title'], '匿名密信抵达')
        self.assertEqual(notes.read_bytes(), before)

    def test_stale_writer_cannot_overwrite(self):
        stale = TimelineStore(self.project)
        self.create()
        before = self.store.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '其他窗口'):
            stale.save_event({'title': '过期写入'})
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_invalid_data_not_reinitialized(self):
        self.store.path.write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '格式无效'):
            TimelineStore(self.project)
        self.assertEqual(self.store.path.read_text(encoding='utf-8'), '{broken')

    def test_written_requires_existing_chapter(self):
        with self.assertRaisesRegex(ValueError, '至少'):
            self.create(state='written')
        with self.assertRaisesRegex(ValueError, '不存在'):
            self.create(chapter_ids=['missing'])

    def test_plans_and_future_facts_cannot_be_background(self):
        planned = self.create()
        future = self.create(state='written', chapter_ids=['chapter_02'])
        for event_id in (planned, future):
            with self.assertRaisesRegex(ValueError, '背景事实'):
                self.store.save_selection('chapter_01', {event_id:'background'})
        with self.assertRaisesRegex(ValueError, '后文章节'):
            self.store.save_selection('chapter_01', {future:'develop'})
        self.store.save_selection('chapter_01', {planned:'develop', future:'withhold'})
        text = self.store.render('chapter_01')
        self.assertIn('计划中', text)
        self.assertIn('暂不揭示', text)
        self.assertIn('不提前实现或泄露', text)

    def test_background_and_selection_survive_restart(self):
        event_id = self.create(state='written', chapter_ids=['chapter_01'])
        self.store.save_selection('chapter_02', {event_id:'background'})
        self.assertIn('背景事实', TimelineStore(self.project).render('chapter_02'))
        self.assertEqual(self.store.render('chapter_01'), '')

    def test_deleted_and_abandoned_are_excluded(self):
        event_id = self.create()
        self.store.save_selection('chapter_01', {event_id:'develop'})
        self.store.set_deleted(event_id, True)
        self.assertEqual(self.store.render('chapter_01'), '')
        self.store.set_deleted(event_id, False)
        self.assertEqual(self.store.selection('chapter_01'), {})
        values = self.store.events()[0]
        values['state'] = 'abandoned'
        self.store.save_event(values, event_id)
        with self.assertRaisesRegex(ValueError, '放弃'):
            self.store.save_selection('chapter_01', {event_id:'develop'})

    def test_edit_clears_usage_and_invalidates_ai_snapshot(self):
        event_id = self.create()
        self.store.save_selection('chapter_01', {event_id:'develop'})
        text = self.project.load_chapter('chapter_01').raw
        snapshot = AIContextSnapshot.capture(self.project,'chapter_01',text,task_kind='expand')
        values = self.store.events()[0]
        values['description'] = '密信被截获'
        self.store.save_event(values, event_id)
        self.assertFalse(snapshot.matches(self.project,'chapter_01',text))
        self.assertEqual(self.store.selection('chapter_01'), {})

    def test_both_writing_prompts_include_selected_only(self):
        selected = self.create(chapter_ids=['chapter_01'])
        self.store.save_event({'title':'未选择的未来政变'})
        self.store.save_selection('chapter_01', {selected:'develop'})
        for builder in (build_expansion_prompt, build_write_prompt):
            result = builder(self.project,'chapter_01',800)
            self.assertIn('密信抵达', result.user_prompt)
            self.assertIn('本次展开', result.user_prompt)
            self.assertNotIn('未选择的未来政变', result.user_prompt)
            report = next(row for row in result.report.sections if row.key == 'timeline_events')
            self.assertEqual(report.status, 'full')
        self.assertEqual(self.store.events()[0]['state'],'planned')

    def test_budget_rejects_partial_event_material(self):
        with self.assertRaisesRegex(ValueError, '完整'):
            allocate([Section('timeline_events','甲'*1000,8000,'whole',0)],500)

    def test_selection_limits_and_failed_write_preserves_selection(self):
        first = self.create()
        self.store.save_selection('chapter_01',{first:'develop'})
        ids = [self.create() for _ in range(9)]
        with self.assertRaisesRegex(ValueError, '最多'):
            self.store.save_selection('chapter_01',{i:'develop' for i in ids})
        self.assertEqual(self.store.selection('chapter_01'),{first:'develop'})
        large = [self.store.save_event({'title':str(i),'description':'字'*4000}) for i in range(2)]
        with self.assertRaisesRegex(ValueError, '过长'):
            self.store.save_selection('chapter_01',{i:'develop' for i in large})

    def test_deleted_chapter_invalidates_background_without_rewriting(self):
        event_id = self.create(state='written',chapter_ids=['chapter_01'])
        self.store.save_selection('chapter_02',{event_id:'background'})
        (self.project.chapters_dir/'chapter_01.md').unlink()
        with self.assertRaisesRegex(ValueError,'背景事实'):
            TimelineStore(self.project).render('chapter_02')

    def test_failed_atomic_save_preserves_file_and_memory(self):
        from unittest.mock import patch
        self.create()
        before = self.store.path.read_bytes()
        with patch('core.timeline_events.atomic_write_text',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.store.save_event({'title':'不可提交'})
        self.assertEqual(self.store.path.read_bytes(),before)
        self.assertEqual(len(self.store.events()),1)

    def test_expansion_and_continuation_cancel_before_generation(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ui.ai_workflow_controller import AIWorkflowController
        chapter = self.project.load_chapter('chapter_01')
        chapter.content = '已有正文，主角来到港口。'
        self.project.save_chapter(chapter.id, chapter.outline, chapter.content)
        for method in (AIWorkflowController.expand, AIWorkflowController.continue_chapter):
            controller = SimpleNamespace(
                _prepare_request=Mock(return_value=(self.project,'chapter_01',Mock())),
                _choose_expansion_context=Mock(return_value=([],[])),
                _choose_event_material=Mock(return_value=False),
                _start=Mock(),config={},parent=None,
            )
            method(controller)
            controller._choose_event_material.assert_called_once_with(self.project,'chapter_01')
            controller._start.assert_not_called()

    def test_selected_event_also_supplies_character_relevance(self):
        character_path = self.project.canon_dir / 'characters' / '林砚.md'
        character_path.write_text('# 林砚\n\n身份：灯塔守望者',encoding='utf-8')
        event_id = self.create(characters='林砚')
        before = build_expansion_prompt(self.project,'chapter_01',800)
        self.assertNotIn('灯塔守望者',before.user_prompt)
        self.store.save_selection('chapter_01',{event_id:'develop'})
        after = build_expansion_prompt(self.project,'chapter_01',800)
        self.assertIn('灯塔守望者',after.user_prompt)

    def test_material_respects_timeline_order_and_stales_previous_results(self):
        first=self.create()
        second=self.store.save_event({'title':'前往灯塔'})
        self.store.save_selection('chapter_01',{second:'develop',first:'develop'})
        text=self.project.load_chapter('chapter_01').raw
        snapshot=AIContextSnapshot.capture(self.project,'chapter_01',text,task_kind='expand')
        rendered=self.store.render('chapter_01')
        self.assertLess(rendered.index('密信抵达'),rendered.index('前往灯塔'))
        self.store.move_to(second,first)
        rendered=self.store.render('chapter_01')
        self.assertLess(rendered.index('前往灯塔'),rendered.index('密信抵达'))
        self.assertFalse(snapshot.matches(self.project,'chapter_01',text))
