"""Exact incremental evidence reuse on temporary projects; no model calls."""
import hashlib
import json
import re
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core.ai_result_service import AIResultService
from core.ai_workflow import AIWorkflowService
from core.chapter_facts import (
    FACT_CACHE_SCHEMA_VERSION, FACT_PROMPT_VERSION, FactLedgerCache,
    _cache_hash, build_chunk_facts_prompt, extract_chapter_fact_ledger,
    memory_chunks, parse_chunk_facts,
)
from core.memory_task import MemoryTaskSession
from core.project import NovelProject
from core.task_controller import AITaskCancelled
from core.text_chunking import (
    bind_memory_chunk_context, chapter_content_hash, chunk_chapter, stable_memory_chunks,
)
from core.token_budget import DEFAULT_TOKEN_ESTIMATOR as EST
from tests.test_chapter_facts import valid_result
from tests.test_chapter_memory import base_state, wire_id, wire_refs
from tests.test_memory_parallel import FakeFactory
from ui.inspector import render_context_reports


def paragraphs(count=180):
    return [f"场景{i:04d}，林舟记录编号{hashlib.sha256(str(i).encode()).hexdigest()[:12]}。"
            + "风穿过街道，队伍按计划继续行进。" * (4 + i % 3) for i in range(count)]


class SourceDSH:
    profile = "test-model-a"

    def __init__(self):
        self.calls = []

    def generate_json(self, system, user, **options):
        self.calls.append(user)
        field = lambda name: re.search(rf"^{name}：(.*)$", user, re.M)[1]
        chapter, chunk, source = field("章节 ID"), field("分块 ID"), field("来源哈希")
        body = user.split("【章节正文分块】\n", 1)[1].split("\n\n【后邻段上下文", 1)[0]
        entries = re.findall(r"\[([^\]]+:p\d{4})\]\n(.*?)(?=\n\n\[|$)", body, re.S)
        return {"type": "chapter_chunk_facts", "schema_version": 1,
            "chapter_id": chapter, "chunk_id": chunk, "source_hash": source,
            "chunk_summary": entries[0][1][:100],
            "facts": [{"category": "event", "subject": "林舟", "predicate": "记录",
                "value": text, "anchor": anchor, "certainty": "explicit"} for anchor, text in entries],
            "unknowns": [{"description": "编号的用途尚未解释。",
                "anchor": entries[0][0] + "-p" + entries[-1][0].rsplit("p", 1)[1]}]}


class WorkflowDSH(SourceDSH):
    input_token_budget = 100000

    def generate_json(self, system, user, **options):
        if "任务类型：chapter_chunk_facts" in user:
            return super().generate_json(system, user, **options)
        if "任务类型：chapter_memory_suggestion" not in user:
            raise AssertionError("unexpected reduction at this fixture budget")
        self.calls.append(user)
        return {"type": "chapter_memory_suggestion", "schema_version": 2,
            "request_id": re.search(r"^请求 ID：(.*)$", user, re.M)[1],
            "wire_request_id": wire_id(user),
            "digest": {"summary": "林舟继续记录沿途事件。",
                "key_events": [{"text": "记录沿途事件", "fact_ids": wire_refs(user)[:1]}],
                **{key: [] for key in ("character_changes", "location_changes", "item_changes",
                                      "relationship_changes", "timeline_changes")}},
            "changes": [], "conflicts": []}


class StableMemoryChunkTests(TestCase):
    def test_stable_chunks_cover_every_paragraph_with_bounded_overlap(self):
        source = paragraphs()
        text = "\n\n".join(source)
        chunks = stable_memory_chunks("chapter_01", text, target_tokens=1200, overlap_tokens=120)
        self.assertGreater(len(chunks), 2)
        self.assertEqual(chunks, stable_memory_chunks("chapter_01", text.replace("\n", "\r\n"),
            target_tokens=1200, overlap_tokens=120))
        for chunk in chunks:
            self.assertLessEqual(chunk.estimated_tokens, 1200)
            self.assertEqual(chunk.text, "\n\n".join(source[chunk.start_paragraph - 1:chunk.end_paragraph]))
            self.assertEqual(len(chunk.evidence_hashes), chunk.end_paragraph - chunk.start_paragraph + 1)
            self.assertFalse(chunk.reuse_scope)
        covered = {n for c in chunks for n in range(c.start_paragraph, c.end_paragraph + 1)}
        self.assertEqual(covered, set(range(1, len(source) + 1)))

    def test_head_insertion_resynchronizes_most_boundaries(self):
        text = "\n\n".join(paragraphs(300))
        original = stable_memory_chunks("chapter_01", text)
        changed = stable_memory_chunks("chapter_01", "新人物抵达城门。\n\n" + text)
        hashes = {chunk.content_hash for chunk in original}
        unchanged = [c for c in changed if c.content_hash in hashes]
        self.assertGreater(len(unchanged), len(changed) * .7)
        self.assertTrue(all(c.start_paragraph > 1 for c in unchanged))

    def test_all_context_and_task_wrapping_are_budgeted_without_truncating(self):
        source = paragraphs(30)
        text = "\n\n".join(source)
        chunks = memory_chunks("chapter_01", text, "第一章", chunk_token_budget=5000,
            overlap_tokens=0, input_token_budget=4000, estimator=EST)
        self.assertEqual("\n\n".join(c.text for c in chunks), text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            prompt = build_chunk_facts_prompt("第一章", c, input_token_budget=4000)
            self.assertLessEqual(prompt.report.estimated_input_tokens, 4000)
            self.assertIn(c.context_before or "（无）", prompt.user_prompt)
            self.assertIn(c.context_after or "（无）", prompt.user_prompt)
        report_html = render_context_reports([prompt.report])
        self.assertIn("邻段参考片段", report_html)
        self.assertNotIn("neighbor_context", report_html)
        self.assertNotIn(source[-1], report_html)

    def test_split_paragraph_and_duplicate_context_remain_snapshot_bound(self):
        for text in ("超长段落" * 1500, "重复段落。\n\n唯一段落。\n\n重复段落。"):
            chunks = stable_memory_chunks("chapter_01", text, target_tokens=300)
            self.assertTrue(all(c.reuse_scope for c in chunks))

    def test_context_excerpt_is_bounded_but_whole_neighbor_hash_is_bound(self):
        first = "林舟看向苏婉。" + "字" * 2000
        body = "他走向北港。"
        def single(source):
            text = source + "\n\n" + body
            chunk = chunk_chapter("chapter_01", body)[0]
            chunk = replace(chunk, start_paragraph=2, end_paragraph=2,
                chunk_id="chapter_01:c0002:p0002-p0002", annotated_text="[chapter_01:p0002]\n" + body)
            return bind_memory_chunk_context((chunk,), text)[0]
        old, new = single(first), single(first.replace("林舟", "苏婉", 1))
        self.assertLessEqual(EST.estimate(old.context_before), 256)
        self.assertEqual(old.context_before, new.context_before)
        self.assertNotEqual(old.context_hashes, new.context_hashes)


class IncrementalFactsTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.project = NovelProject.create(self.root / "project", "测试")
        self.cache = FactLedgerCache(self.project, self.root / "facts")
        self.dsh = SourceDSH()
        self.source = paragraphs(180)

    def save(self, source, title="第一章"):
        self.project.save_chapter("chapter_01", outline="", title=title, content="\n\n".join(source))

    def extract(self, **options):
        return extract_chapter_fact_ledger(self.project, "chapter_01", self.dsh,
            cache=self.cache, chunk_token_budget=1200, overlap_tokens=120, **options)

    def snapshot(self):
        return {str(p.relative_to(self.project.root)): p.read_bytes()
                for p in self.project.root.rglob("*") if p.is_file()}

    def assert_current_evidence(self, ledger, source):
        for fact in ledger.facts:
            number = int(fact.anchor.rsplit("p", 1)[1])
            self.assertEqual(fact.value, source[number - 1])
        for result in ledger.chunks:
            for unknown in result.unknowns:
                match = re.fullmatch(r"chapter_01:p(\d{4})-p(\d{4})", unknown.anchor)
                self.assertIsNotNone(match)
                self.assertLessEqual(int(match[1]), int(match[2]))
        self.assertEqual(ledger.chapter_hash, chapter_content_hash("\n\n".join(source)))

    def test_head_insert_reuses_facts_rebases_ids_ranges_and_never_writes_project(self):
        self.save(self.source)
        first = self.extract()
        changed = ["新的开场，苏婉站在城门边。", *self.source]
        self.save(changed)
        snapshot = self.snapshot()
        calls = len(self.dsh.calls)
        second = self.extract()
        self.assertGreater(second.cache_hits, len(second.chunks) * .7)
        self.assertEqual(len(self.dsh.calls) - calls, second.extracted_chunks)
        self.assert_current_evidence(second, changed)
        old_ids = {f.value: f.fact_id for f in first.facts}
        self.assertTrue(all(f.fact_id != old_ids.get(f.value) for f in second.facts))
        self.assertEqual(self.snapshot(), snapshot)
        calls = len(self.dsh.calls)
        repeat = self.extract()
        self.assertEqual(repeat.cache_hits, len(second.chunks))
        self.assertEqual(len(self.dsh.calls), calls)

    def test_local_edit_and_deletion_preserve_only_exact_evidence(self):
        self.save(self.source)
        self.extract()
        changed = list(self.source)
        changed[40] = "林舟改变计划，队伍返回旧站。"
        self.save(changed)
        edited = self.extract()
        self.assertGreater(edited.cache_hits, len(edited.chunks) // 2)
        self.assertGreater(edited.extracted_chunks, 0)
        self.assert_current_evidence(edited, changed)
        del changed[10:13]
        self.save(changed)
        deleted = self.extract()
        self.assertGreater(deleted.cache_hits, len(deleted.chunks) // 2)
        self.assert_current_evidence(deleted, changed)

    def test_changed_neighbor_invalidates_equal_chunk_and_preserves_distant_chunks(self):
        self.save(self.source)
        self.extract()
        old_chunks = stable_memory_chunks("chapter_01", "\n\n".join(self.source),
            target_tokens=1200, overlap_tokens=120)
        target = next(c for c in old_chunks if 40 < c.start_paragraph < 100)
        changed = list(self.source)
        changed[target.end_paragraph] = changed[target.end_paragraph].replace("林舟", "苏婉", 1)
        self.save(changed)
        calls = len(self.dsh.calls)
        second = self.extract()
        self.assertGreater(second.extracted_chunks, 0)
        self.assertGreater(second.cache_hits, 0)
        new_chunks = stable_memory_chunks("chapter_01", "\n\n".join(changed),
            target_tokens=1200, overlap_tokens=120)
        same = next(c for c in new_chunks if c.content_hash == target.content_hash)
        self.assertNotEqual(target.context_hashes, same.context_hashes)
        self.assertNotEqual(self.cache._path(target), self.cache._path(same))
        self.assertTrue(any(f"来源哈希：{target.content_hash}" in call for call in self.dsh.calls[calls:]))

    def test_title_configuration_and_force_refresh_invalidate_extraction(self):
        self.save(self.source[:15])
        self.extract()
        self.save(self.source[:15], title="改过的标题")
        changed_title = self.extract()
        self.assertEqual(changed_title.cache_hits, 0)
        self.dsh.profile = "test-model-b"
        changed_model = self.extract()
        self.assertEqual(changed_model.cache_hits, 0)
        self.assertEqual(self.extract().extracted_chunks, 0)
        self.assertEqual(self.extract(force_refresh=True).cache_hits, 0)

    def test_cancelled_late_result_is_not_cached(self):
        self.save(self.source[:20])
        cancel = threading.Event()
        original = self.dsh.generate_json
        def cancelled(*args, **kwargs):
            raw = original(*args, **kwargs)
            cancel.set()
            return raw
        self.dsh.generate_json = cancelled
        with self.assertRaises(AITaskCancelled):
            self.extract(cancel_event=cancel)
        self.assertFalse(list(self.cache.project_dir.rglob("*.json")))

    def test_cancel_during_cache_rebase_does_not_return_cached_ledger(self):
        self.save(self.source[:20])
        self.extract()
        calls = len(self.dsh.calls)
        cancel, original = threading.Event(), self.cache.load
        def cancelled(chunk, **options):
            result = original(chunk, **options)
            cancel.set()
            return result
        with patch.object(self.cache, "load", side_effect=cancelled):
            with self.assertRaises(AITaskCancelled):
                self.extract(cancel_event=cancel)
        self.assertEqual(len(self.dsh.calls), calls)

    def test_incremental_workflow_never_reuses_old_proposal_or_accepts_stale_source(self):
        self.save(self.source)
        self.project.save_story_state(base_state())
        dsh, events = WorkflowDSH(), []
        workflow = AIWorkflowService(dsh, input_token_budget=100000,
            chunk_token_budget=1200, chunk_overlap_tokens=120,
            fact_cache_root=self.root / "workflow-facts", memory_cache_root=self.root / "memory",
            progress_callback=events.append)
        original = workflow.update_memory(self.project, "chapter_01")
        self.assertFalse(original.has_blockers)
        calls = len(dsh.calls)
        self.assertTrue(workflow.update_memory(self.project, "chapter_01").cache_hit)
        self.assertEqual(calls, len(dsh.calls))
        self.save(["新开场。", *self.source])
        before = self.snapshot()
        changed = workflow.update_memory(self.project, "chapter_01")
        self.assertFalse(changed.cache_hit)
        self.assertNotEqual(changed.chapter_hash, original.chapter_hash)
        self.assertNotEqual(changed.ledger_hash, original.ledger_hash)
        self.assertTrue(any(e.stage == "facts" and e.state == "cached" for e in events))
        self.assertEqual(self.snapshot(), before)
        with self.assertRaisesRegex(ValueError, "正文.*变化"):
            AIResultService.commit_memory_proposal(self.project, "chapter_01", original)
        self.assertEqual(self.snapshot(), before)
        state = self.project.load_story_state()
        state["characters"]["林舟"]["items"].append("新物品")
        self.project.save_story_state(state)
        calls = len(dsh.calls)
        new_state = workflow.update_memory(self.project, "chapter_01")
        self.assertFalse(new_state.cache_hit)
        self.assertNotEqual(new_state.base_state_hash, changed.base_state_hash)
        self.assertEqual(len(dsh.calls) - calls, 1)

    def test_serial_parallel_share_incremental_cache_and_order(self):
        self.save(self.source)
        factory = FakeFactory()
        # Keep the captured settings identical in both modes. This fake's normal
        # result contains chunk IDs, so use position-independent source facts.
        factory.profile = self.dsh.profile
        factory.generate = lambda lane, system, user, options: self.dsh.generate_json(system, user, **options)
        session = MemoryTaskSession(factory, 30, None, None, concurrency=2)
        self.addCleanup(session.close)
        first = extract_chapter_fact_ledger(self.project, "chapter_01", session,
            cache=self.cache, chunk_token_budget=1200, overlap_tokens=120)
        session.close()
        self.assertEqual(len(factory.clients), 2)
        same = self.extract()
        self.assertEqual(same.cache_hits, len(first.chunks))
        self.assertEqual(same.facts, first.facts)
        shifted = ["新开场。", *self.source]
        self.save(shifted)
        rebased = self.extract()
        self.assertGreater(rebased.cache_hits, len(rebased.chunks) // 2)
        self.assert_current_evidence(rebased, shifted)


class RelativeFactCacheTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.cache = FactLedgerCache(NovelProject.create(root / "project", "测试"), root / "cache")
        self.chunk = bind_memory_chunk_context(chunk_chapter("chapter_01", "唯一正文。"), "唯一正文。")[0]
        self.result = parse_chunk_facts(valid_result(self.chunk), self.chunk)
        self.cache.save(self.chunk, self.result)

    def test_cache_stores_relative_positions_without_fact_ids_or_source_text(self):
        data = json.loads(self.cache._path(self.chunk).read_text("utf-8"))
        self.assertEqual(data["schema_version"], FACT_CACHE_SCHEMA_VERSION)
        self.assertEqual(data["identity"]["prompt_version"], FACT_PROMPT_VERSION)
        self.assertEqual(data["result"]["facts"][0]["relative_anchor"], [0, 0])
        self.assertNotIn("fact_id", data["result"]["facts"][0])
        self.assertNotIn("唯一正文", json.dumps(data, ensure_ascii=False))
        self.assertEqual(self.cache.load(self.chunk), self.result)

    def test_repeated_paragraphs_cannot_move_cached_evidence(self):
        text = "重复。\n\n重复。"
        paragraph = chunk_chapter("chapter_01", "重复。")[0]
        original = bind_memory_chunk_context((paragraph,), text)[0]
        shifted = replace(paragraph, start_paragraph=2, end_paragraph=2,
            chunk_id="chapter_01:c0002:p0002-p0002", annotated_text="[chapter_01:p0002]\n重复。")
        shifted = bind_memory_chunk_context((shifted,), "新开场。\n\n" + text)[0]
        self.assertEqual(original.content_hash, shifted.content_hash)
        self.cache.save(original, parse_chunk_facts(valid_result(original), original))
        self.assertIsNone(self.cache.load(shifted))
        self.assertIsNotNone(self.cache.load(original))

    def test_changed_pronoun_context_cannot_reuse_equal_evidence(self):
        chunk = replace(self.chunk, start_paragraph=2, end_paragraph=2,
            chunk_id="chapter_01:c0002:p0002-p0002", annotated_text="[chapter_01:p0002]\n唯一正文。")
        original = bind_memory_chunk_context((chunk,), "林舟走向北港。\n\n唯一正文。")[0]
        changed = bind_memory_chunk_context((chunk,), "苏婉走向北港。\n\n唯一正文。")[0]
        self.assertEqual(original.content_hash, changed.content_hash)
        self.cache.save(original, parse_chunk_facts(valid_result(original), original))
        self.assertIsNone(self.cache.load(changed))

    def test_position_sensitive_free_text_is_never_rewritten_or_moved(self):
        shifted = replace(self.chunk, chunk_id="chapter_01:c0002:p0002-p0002",
            start_paragraph=2, end_paragraph=2, annotated_text="[chapter_01:p0002]\n唯一正文。")
        for value in (self.chunk.chunk_id, "证据p0001", "编号C0001"):
            with self.subTest(value=value):
                bound = parse_chunk_facts(valid_result(self.chunk, value=value), self.chunk)
                self.cache.save(self.chunk, bound)
                self.assertEqual(self.cache._path(self.chunk), self.cache._path(shifted))
                self.assertIsNone(self.cache.load(shifted))
                self.assertEqual(self.cache.load(self.chunk), bound)

    def test_corruption_wrong_identity_and_old_cache_are_misses(self):
        path = self.cache._path(self.chunk)
        original = json.loads(path.read_text("utf-8"))
        bad_values = [[], {**original, "schema_version": 1}, self.result.to_dict(),
            {**original, "identity": {**original["identity"], "context_hashes": ["wrong"]}},
            {**original, "result_hash": "wrong"}]
        for bad in bad_values:
            path.write_text(json.dumps(bad), encoding="utf-8")
            self.assertIsNone(self.cache.load(self.chunk))
        path.write_text("{broken", encoding="utf-8")
        self.assertIsNone(self.cache.load(self.chunk))

    def test_invalid_relative_offsets_are_rejected_even_with_consistent_checksum(self):
        path = self.cache._path(self.chunk)
        original = json.loads(path.read_text("utf-8"))
        for offsets in ([-1, 0], [0, 1], [True, 0], [0.0, 0], [0], "p0001", [1, 0]):
            data = json.loads(json.dumps(original))
            data["result"]["facts"][0]["relative_anchor"] = offsets
            data["result_hash"] = _cache_hash(data["result"])
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(self.cache.load(self.chunk))

    def test_protocol_version_and_transport_context_changes_are_misses(self):
        with patch("core.chapter_facts.FACT_PROMPT_VERSION", FACT_PROMPT_VERSION + 1):
            self.assertIsNone(self.cache.load(self.chunk))
        self.assertIsNone(self.cache.load(replace(self.chunk, context_before="不同的指代上下文。")))
        self.assertIsNone(self.cache.load(replace(self.chunk, chunk_strategy="memory-cdc-v2")))
