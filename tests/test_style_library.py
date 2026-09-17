import copy
import json
from pathlib import Path
import tempfile
import unittest
from core.style_library import *
from core.writing_style import load_style, render_style

class StyleLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.value=empty_library();self.value["enabled"]=True
        self.value["profile"]={"节奏":"保持克制"}
        self.value["samples"]=[self.sample("dialogue", "对话"),self.sample("general", "通用")]
    def sample(self, id, scene):
        return {"id":id,"title":"例文","source":"作者自编 v1","text":"他将门推开，又停住。","traits":"动作表现犹疑","scene":scene,"enabled":True}
    def save(self):
        return save_library(self.root,self.value,expected_revision=revision(self.root))
    def test_version_history_and_stale(self):
        first=self.save();self.assertEqual(first["samples"][0]["version"],1)
        stale=revision(self.root);self.value=first;self.value["samples"][0]["text"]+="雨停了。"
        second=self.save();self.assertEqual(second["samples"][0]["version"],2)
        self.assertEqual(second["history"][-1]["samples"][0]["text"],"他将门推开，又停住。")
        with self.assertRaises(ValueError):save_library(self.root,first,expected_revision=stale)
        self.assertEqual(load_library(self.root),second)
    def test_scene_budget_provenance(self):
        self.value["chapter_scenes"]={"chapter_01":"对话"};self.save()
        rendered=render_library(self.root,"chapter_01")
        self.assertIn("id=dialogue",rendered);self.assertIn("id=general",rendered)
        self.assertNotIn("id=dialogue",render_library(self.root,"chapter_02"))
        self.assertNotIn("STYLE_SAMPLE",render_library(self.root,"chapter_01",budget=159))
        usage=sample_usage(self.root,rendered,"chapter_01")
        self.assertTrue(all(row["status"]=="included" for row in usage))
        self.assertNotIn("text",usage[0])
    def test_disabled_and_no_partial_sample(self):
        self.value["enabled"]=False;self.save();self.assertEqual(load_style(self.root),"")
        self.assertNotIn("STYLE_SAMPLE",render_style("画像\n<STYLE_SAMPLE id=x>半条"))
    def test_guide_priority_and_limits(self):
        self.save();path=self.root/"writing/style_guide.md";path.write_text("作者要求"*700,encoding="utf-8")
        self.assertEqual(load_style(self.root),"作者要求"*700)
    def test_reject_invalid_payload_without_write(self):
        self.value["samples"][0]["text"]="x"*4001
        with self.assertRaises(ValueError):self.save()
        self.assertFalse((self.root/LIBRARY_PATH).exists())
    def test_task_snapshot_tracks_library(self):
        from core.task_context import context_paths
        from core.project import NovelProject
        project=NovelProject.create(self.root/"project","test")
        from core.task_context import AIContextSnapshot
        before=AIContextSnapshot.capture(project,"chapter_01","正文",task_kind="continuation")
        self.assertIn(project.root/LIBRARY_PATH,context_paths(project,"chapter_01",task_kind="continuation"))
        save_library(project.root,self.value,expected_revision="")
        after=AIContextSnapshot.capture(project,"chapter_01","正文",task_kind="continuation")
        self.assertNotEqual(before.context_hash,after.context_hash)
    def test_generation_report_matches_actual_samples(self):
        from core.project import NovelProject
        from core.prompt_builder import build_expansion_prompt
        project=NovelProject.create(self.root/"project","test")
        self.value["chapter_scenes"]={"chapter_01":"对话"}
        save_library(project.root,self.value,expected_revision="")
        bundle=build_expansion_prompt(project,"chapter_01",1000)
        self.assertIn("STYLE_SAMPLE",bundle.user_prompt)
        self.assertEqual(len(bundle.report.style_samples),2)
        self.assertTrue(all(s["status"]=="included" for s in bundle.report.style_samples))

class EvaluationTests(unittest.TestCase):
    def test_dry_run_is_original_24_cases(self):
        from scripts.evaluate_prose import run
        with tempfile.TemporaryDirectory() as tmp:
            result=run(Path(tmp)/"run",dry_run=True)
            self.assertEqual(result["cases"],24);self.assertEqual(result["status"],"dry_run")
            with self.assertRaises(FileExistsError):run(Path(tmp)/"run",dry_run=True)
    def test_fake_model_trial_produces_blind_review(self):
        from scripts.evaluate_prose import run
        class Client:
            def generate(self,*a,**kw):return "他将门推开，又停住。"
            def prompt_build_budget(self):return 24000
            def generate_json(self,*a,**kw):return {"type":"prose_proposals","patches":[]}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"run";result=run(path,limit=1,client=Client())
            self.assertEqual(result["status"],"complete_pending_human_review",result)
            self.assertEqual(len(list((path/"blind").glob("*.txt"))),3)
            self.assertEqual(len(result["metrics"]),3)
            self.assertEqual(result["human_assessment"],"pending")
