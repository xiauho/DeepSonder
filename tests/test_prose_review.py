from tests.style_fixtures import set_style
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock
from core.project import NovelProject
from core.prose_review import (ProsePatch, apply_patches, parse_patches, prose_bounds,
    validate_scope, run_prose_task, local_findings)
from core.writing_style import load_style, save_exceptions, load_exceptions
from core.task_context import AIContextSnapshot
from core.task_controller import AITaskCancelled

class ProseReviewTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = NovelProject.create(Path(self.tmp.name)/'novel', '测试')
        self.source = '# 章节\n\n## 大纲\n保留规划\n\n## 正文\n他把门推开。\n她站在窗边。\n\n## 作者备注\n保留备注\n'
        self.start, self.end = prose_bounds(self.source)

    def payload(self, old='他把门推开。', new='他推开门。'):
        return {'type':'prose_proposals','patches':[{'expected_original':old,'replacement':new,'reason':'减少重复修饰'}]}

    def test_only_selected_range_changes_and_metadata_is_preserved(self):
        patches=parse_patches(self.payload(),self.source,self.start,self.end,'style_review')
        updated=apply_patches(self.source,patches)
        self.assertEqual(updated,self.source.replace('他把门推开。','他推开门。'))
        self.assertIn('## 作者备注\n保留备注',updated)

    def test_invalid_and_ambiguous_anchors_rejected(self):
        for source,payload in [(self.source,self.payload('保留规划')),
                               ('重复的原文。重复的原文。',self.payload('重复的原文。'))]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                a,b=prose_bounds(source)
                parse_patches(payload,source,a,b,'style_review')

    def test_overlaps_rejected_before_any_change(self):
        payload=self.payload()
        payload['patches'].append({'expected_original':'把门','replacement':'开门','reason':'测试'})
        with self.assertRaises(ValueError):
            parse_patches(payload,self.source,self.start,self.end,'style_review')

    def test_stale_source_and_malformed_patch_cannot_apply(self):
        patches=parse_patches(self.payload(),self.source,self.start,self.end,'style_review')
        with self.assertRaises(ValueError):
            apply_patches('改动'+self.source,patches)
        with self.assertRaises(ValueError):
            apply_patches(self.source,(ProsePatch(-1,2,'','x',''),))

    def test_scope_rejects_metadata_and_oversized_text(self):
        with self.assertRaises(ValueError):
            validate_scope(self.source,0,self.end)
        with self.assertRaises(ValueError):
            validate_scope('字'*12001,0,12001)

    def test_expansion_requires_entire_selection_and_growth(self):
        text='他推开门。'
        with self.assertRaises(ValueError):
            parse_patches(self.payload(text,'他开门。'),text,0,len(text),'selection_expand')
        p=parse_patches(self.payload(text,'他伸出手，把沉重的门推开。'),text,0,len(text),'selection_expand')
        self.assertEqual(len(p),1)

    def test_whitelist_suppresses_intersecting_patch_and_local_warning(self):
        self.assertEqual(parse_patches(self.payload(),self.source,self.start,self.end,'style_review',('把门',)),())
        self.assertFalse(local_findings('他不知道的是',('他不知道的是',)))
        self.assertTrue(local_findings('他不知道的是'))

    def test_protocol_markup_rejected(self):
        for replacement in ('# 新标题','<NOVEL_TEXT>正文</NOVEL_TEXT>','```正文'):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                parse_patches(self.payload(new=replacement),self.source,self.start,self.end,'style_review')

    def test_style_and_exception_changes_invalidate_task(self):
        snap=AIContextSnapshot.capture(self.project,'chapter_01',self.source,task_kind='style_review')
        save_exceptions(self.project.root,['原句'])
        self.assertFalse(snap.matches(self.project,'chapter_01',self.source,task_kind='style_review'))
        snap=AIContextSnapshot.capture(self.project,'chapter_01',self.source,task_kind='selection_expand')
        set_style(self.project.root, '克制，保留人物口吻')
        self.assertFalse(snap.matches(self.project,'chapter_01',self.source,task_kind='selection_expand'))

    def test_exception_roundtrip_and_malformed_file_fails_closed(self):
        save_exceptions(self.project.root,['原句','原句'])
        self.assertEqual(load_exceptions(self.project.root),('原句',))
        (self.project.root/'writing/style_exceptions.json').write_text('{}',encoding='utf-8')
        with self.assertRaises(ValueError): load_exceptions(self.project.root)

    def test_one_generation_call_style_and_request_injected_no_write(self):
        set_style(self.project.root, '# 风格\n克制冷静\n')
        dsh=Mock(); dsh.prompt_build_budget.return_value=20000; dsh.generate_json.return_value=self.payload()
        before=self.project.load_chapter('chapter_01').raw
        result=run_prose_task(self.project,'chapter_01',dsh,source=self.source,start=self.start,end=self.end,
                              kind='style_review',request='保留心理描写')
        self.assertEqual(len(result.patches),1)
        dsh.generate_json.assert_called_once()
        user=dsh.generate_json.call_args.args[1]
        self.assertIn('克制冷静',user); self.assertIn('保留心理描写',user)
        self.assertEqual(self.project.load_chapter('chapter_01').raw,before)

    def test_budget_failure_does_not_send_truncated_selection(self):
        dsh=Mock(); dsh.prompt_build_budget.return_value=100
        with self.assertRaises(ValueError):
            run_prose_task(self.project,'chapter_01',dsh,source=self.source,start=self.start,end=self.end,kind='style_review')
        dsh.generate_json.assert_not_called()

    def test_cancel_before_and_after_model_returns(self):
        event=threading.Event(); event.set()
        dsh=Mock(); dsh.prompt_build_budget.return_value=20000
        with self.assertRaises(AITaskCancelled):
            run_prose_task(self.project,'chapter_01',dsh,source=self.source,start=self.start,end=self.end,kind='style_review',cancel_event=event)
        dsh.generate_json.assert_not_called()
        event.clear()
        def finish(*args,**kwargs):
            event.set(); return self.payload()
        dsh.generate_json.side_effect=finish
        with self.assertRaises(AITaskCancelled):
            run_prose_task(self.project,'chapter_01',dsh,source=self.source,start=self.start,end=self.end,kind='style_review',cancel_event=event)
