"""Memory recovery contracts: no live model and no personal project writes."""
import json
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core.ai_workflow import AIWorkflowService
from core.ai_result_service import AIResultService
from core.chapter_facts import FactRecord
from core.chapter_memory import (partial_memory_proposal, parse_memory_proposal,
                                 ChapterMemoryCache, MemoryConflict, canonical_hash)
from core.project import NovelProject
from core.task_controller import AITaskCancelled
from core.text_chunking import chapter_content_hash
from test_chapter_memory import make_ledger, base_state, valid_proposal, _MemoryV2DSH


def mixed_proposal(content="林舟得到铜钱。林舟未到达北港。"):
    ledger = make_ledger(chapter_content_hash(content))
    failed = replace(ledger.facts[0], predicate="未到达")
    safe = FactRecord("fact_coin", "item", "林舟", "得到", "铜钱", "chapter_01:p0002", "explicit")
    ledger = replace(ledger, facts=(failed, safe))
    state = base_state()
    raw = valid_proposal(ledger, state)
    raw["changes"].append(dict(kind="add_character_item", subject="林舟", field="", value="铜钱",
                               evidence_fact_ids=["fact_coin"], certainty="explicit"))
    raw["digest"]["key_events"].append(dict(text="林舟得到铜钱", fact_ids=["fact_coin"]))
    return parse_memory_proposal(raw, ledger, state, expected_context_hash="context-hash"), state


class PartialMemoryTests(TestCase):
    def test_independent_subset_rebuilds_summary_and_keeps_original_immutable(self):
        original, state = mixed_proposal()
        before = canonical_hash(original.to_cache_dict())
        result = partial_memory_proposal(original, state)
        self.assertIsNotNone(result)
        self.assertFalse(result.has_blockers)
        self.assertEqual(len(result.patches), 1)
        self.assertEqual(result.resulting_state["characters"]["林舟"]["location"], "旧站")
        self.assertIn("铜钱", result.resulting_state["characters"]["林舟"]["items"])
        self.assertNotIn("北港", result.summary)
        self.assertIn("部分记忆", result.summary)
        self.assertFalse(result.digest.location_changes)
        self.assertEqual([f.fact_id for f in result.evidence_facts], ["fact_coin"])
        self.assertEqual(before, canonical_hash(original.to_cache_dict()))
        self.assertTrue(original.has_blockers)

    def test_global_conflicts_and_missing_independent_summary_cannot_be_bypassed(self):
        original, state = mixed_proposal()
        for kind in ("fact_collision", "canon_conflict", "state_precondition", "timeline_conflict"):
            conflict = MemoryConflict("global", kind, "blocker", "all", "不可隔离的冲突")
            self.assertIsNone(partial_memory_proposal(replace(original, conflicts=(*original.conflicts, conflict)), state))
        no_events = replace(original, digest=replace(original.digest, key_events=original.digest.key_events[:1]))
        self.assertIsNone(partial_memory_proposal(no_events, state))
        with self.assertRaisesRegex(ValueError, "已变化"):
            partial_memory_proposal(original, {**state, "current_location": "别处"})

    def test_shared_evidence_excludes_dependent_updates(self):
        original, state = mixed_proposal()
        dependent = replace(original.patches[1], evidence_fact_ids=("fact_coin", "fact_location"))
        original = replace(original, patches=(original.patches[0], dependent))
        self.assertIsNone(partial_memory_proposal(original, state))

    def test_partial_commit_retains_guards_and_excluded_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp)/"project", "测试")
            content = "林舟得到铜钱。林舟未到达北港。"
            project.save_chapter("chapter_01", outline="", content=content)
            original, state = mixed_proposal(content)
            project.save_story_state(state)
            subset = partial_memory_proposal(original, state)
            project.save_chapter("chapter_01", outline="", content=content+"正文变化。")
            with self.assertRaisesRegex(ValueError, "正文"):
                AIResultService.commit_memory_proposal(project, "chapter_01", subset)
            self.assertEqual(project.load_story_state(), state)
            project.save_chapter("chapter_01", outline="", content=content)
            AIResultService.commit_memory_proposal(project, "chapter_01", subset)
            self.assertEqual(project.load_story_state()["characters"]["林舟"]["location"], "旧站")
            self.assertIn("铜钱", project.load_story_state()["characters"]["林舟"]["items"])
            self.assertNotIn("北港", project.load_chapter_summaries()["chapter_01"])


class MemoryRetryTests(TestCase):
    def test_targeted_retry_feedback_and_full_refresh_do_not_write_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = NovelProject.create(root/"project", "测试")
            project.save_chapter("chapter_01", outline="", content="林舟抵达北港。")
            dsh = _MemoryV2DSH()
            workflow = AIWorkflowService(dsh, fact_cache_root=root/"facts", memory_cache_root=root/"memory")
            snapshot = {p.relative_to(project.root):p.read_bytes() for p in project.root.rglob("*") if p.is_file()}
            workflow.update_memory(project, "chapter_01")
            self.assertEqual(len(dsh.calls), 2)
            workflow.update_memory(project, "chapter_01", retry_feedback="位置未通过：请核对到达证据")
            self.assertEqual(len(dsh.calls), 3)
            self.assertIn("位置未通过", dsh.calls[-1])
            with patch.object(ChapterMemoryCache, "load_reduction", side_effect=AssertionError("must bypass")):
                workflow.update_memory(project, "chapter_01", refresh_facts=True)
            self.assertEqual(len(dsh.calls), 5)
            self.assertIn("任务类型：chapter_chunk_facts", dsh.calls[-2])
            self.assertEqual(snapshot, {p.relative_to(project.root):p.read_bytes() for p in project.root.rglob("*") if p.is_file()})
            cached = "".join(p.read_text("utf-8") for p in (root/"memory").rglob("*.json"))
            self.assertNotIn("位置未通过", cached)
            cancel = threading.Event(); cancel.set()
            with self.assertRaises(AITaskCancelled):
                workflow.update_memory(project, "chapter_01", refresh_facts=True, cancel_event=cancel)
            self.assertEqual(len(dsh.calls), 5)
