import json
import re
import tempfile
import threading
from pathlib import Path
from unittest import TestCase

from core.ai_workflow import AIWorkflowService
from core.chapter_facts import (
    FactLedgerCache,
    FactProtocolError,
    build_chunk_facts_prompt,
    merge_chunk_facts,
    parse_chunk_facts,
)
from core.project import NovelProject
from core.task_controller import AITaskCancelled
from core.text_chunking import chunk_chapter


def valid_result(chunk, *, value="抵达旧站"):
    return {
        "type": "chapter_chunk_facts",
        "schema_version": 1,
        "chapter_id": chunk.chapter_id,
        "chunk_id": chunk.chunk_id,
        "source_hash": chunk.content_hash,
        "chunk_summary": "林舟抵达旧站。",
        "facts": [
            {
                "category": "event",
                "subject": "林舟",
                "predicate": "抵达",
                "value": value,
                "anchor": f"{chunk.chapter_id}:p{chunk.start_paragraph:04d}",
                "certainty": "explicit",
            }
        ],
        "unknowns": [],
        "completion_message": "分块事实提取完成",
    }


class ChunkFactProtocolTests(TestCase):
    def setUp(self) -> None:
        self.chunk = chunk_chapter("chapter_01", "林舟抵达旧站。\n\n他发现门已锁住。")[0]

    def test_valid_result_is_normalized_and_fact_id_is_local(self) -> None:
        result = valid_result(self.chunk)
        result["facts"][0]["fact_id"] = "model-controlled-id"
        parsed = parse_chunk_facts(result, self.chunk)
        self.assertTrue(parsed.facts[0].fact_id.startswith("fact_"))
        self.assertNotEqual(parsed.facts[0].fact_id, "model-controlled-id")
        self.assertEqual(parsed.facts[0].certainty, "explicit")

    def test_wrong_hash_anchor_or_certainty_is_rejected(self) -> None:
        wrong_hash = valid_result(self.chunk)
        wrong_hash["source_hash"] = "stale"
        with self.assertRaisesRegex(FactProtocolError, "source_hash"):
            parse_chunk_facts(wrong_hash, self.chunk)

        wrong_anchor = valid_result(self.chunk)
        wrong_anchor["facts"][0]["anchor"] = "chapter_01:p9999"
        with self.assertRaisesRegex(FactProtocolError, "超出"):
            parse_chunk_facts(wrong_anchor, self.chunk)

        wrong_certainty = valid_result(self.chunk)
        wrong_certainty["facts"][0]["certainty"] = "maybe"
        with self.assertRaisesRegex(FactProtocolError, "certainty"):
            parse_chunk_facts(wrong_certainty, self.chunk)

    def test_merge_deduplicates_overlap_facts(self) -> None:
        first = parse_chunk_facts(valid_result(self.chunk), self.chunk)
        second = parse_chunk_facts(valid_result(self.chunk), self.chunk)
        facts, unknowns = merge_chunk_facts([first, second])
        self.assertEqual(len(facts), 1)
        self.assertEqual(unknowns, ())

    def test_merge_keeps_strongest_certainty_for_duplicate_fact(self) -> None:
        weak_value = valid_result(self.chunk)
        weak_value["facts"][0]["certainty"] = "uncertain"
        weak = parse_chunk_facts(weak_value, self.chunk)
        strong = parse_chunk_facts(valid_result(self.chunk), self.chunk)

        facts, _unknowns = merge_chunk_facts([weak, strong])

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].certainty, "explicit")

    def test_prompt_has_token_report_and_rejects_too_small_budget(self) -> None:
        prompt = build_chunk_facts_prompt("第一章", self.chunk)
        self.assertEqual(prompt.report.task_kind, "chapter_chunk_facts")
        self.assertGreater(prompt.report.estimated_input_tokens, 0)
        self.assertEqual(prompt.report.token_estimator, "conservative_v1")

        large = chunk_chapter(
            "chapter_02",
            "字" * 900,
            target_tokens=2_000,
            overlap_tokens=0,
        )[0]
        with self.assertRaisesRegex(RuntimeError, "token 预算"):
            build_chunk_facts_prompt("第二章", large, input_token_budget=1_000)


class FactLedgerCacheTests(TestCase):
    def test_cache_round_trip_does_not_store_raw_chunk_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = NovelProject.create(root / "project", "测试")
            secret = "RAW_CHAPTER_SECRET_9C2C"
            chunk = chunk_chapter("chapter_01", secret + "只用于缓存测试。")[0]
            result = parse_chunk_facts(valid_result(chunk), chunk)
            cache = FactLedgerCache(project, root / "cache")
            cache.save(chunk, result)
            loaded = cache.load(chunk)

            self.assertEqual(loaded, result)
            cache_text = next((root / "cache").rglob("*.json")).read_text(
                encoding="utf-8"
            )
            self.assertNotIn(secret, cache_text)

    def test_changed_chunk_hash_cannot_reuse_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = NovelProject.create(root / "project", "测试")
            original = chunk_chapter("chapter_01", "林舟抵达旧站。")[0]
            changed = chunk_chapter("chapter_01", "林舟抵达新站。")[0]
            cache = FactLedgerCache(project, root / "cache")
            cache.save(original, parse_chunk_facts(valid_result(original), original))

            self.assertEqual(original.chunk_id, changed.chunk_id)
            self.assertNotEqual(original.content_hash, changed.content_hash)
            self.assertIsNone(cache.load(changed))


class _LedgerDSH:
    def __init__(self):
        self.calls = []

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        self.calls.append((system_prompt, user_prompt, kwargs.get("context_report")))
        chapter_id = re.search(r"^章节 ID：(.*)$", user_prompt, re.MULTILINE).group(1)
        chunk_id = re.search(r"^分块 ID：(.*)$", user_prompt, re.MULTILINE).group(1)
        source_hash = re.search(r"^来源哈希：(.*)$", user_prompt, re.MULTILINE).group(1)
        start = re.search(r"允许锚点范围：p(\d{4})", user_prompt).group(1)
        return {
            "type": "chapter_chunk_facts",
            "schema_version": 1,
            "chapter_id": chapter_id,
            "chunk_id": chunk_id,
            "source_hash": source_hash,
            "chunk_summary": "本分块发生了可定位事件。",
            "facts": [
                {
                    "category": "event",
                    "subject": "林舟",
                    "predicate": "行动",
                    "value": chunk_id,
                    "anchor": f"{chapter_id}:p{start}",
                    "certainty": "explicit",
                }
            ],
            "unknowns": [],
        }


class ChapterFactLedgerWorkflowTests(TestCase):
    def test_workflow_extracts_every_chunk_then_reuses_validated_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = NovelProject.create(root / "project", "测试")
            content = "\n\n".join(
                f"P{index:02d}_林舟继续行动。" + "字" * 80
                for index in range(1, 13)
            )
            project.save_chapter(
                "chapter_01",
                outline="测试分块",
                content=content,
                title="第一章",
            )
            old_state = json.dumps(project.load_story_state(), sort_keys=True)
            old_summaries = json.dumps(project.load_chapter_summaries(), sort_keys=True)
            dsh = _LedgerDSH()
            workflow = AIWorkflowService(
                dsh,
                input_token_budget=4_000,
                chunk_token_budget=300,
                chunk_overlap_tokens=60,
                fact_cache_root=root / "cache",
            )

            first = workflow.build_chapter_fact_ledger(project, "chapter_01")
            first_call_count = len(dsh.calls)
            second = workflow.build_chapter_fact_ledger(project, "chapter_01")

            self.assertGreater(first_call_count, 1)
            self.assertEqual(first.extracted_chunks, first_call_count)
            self.assertEqual(first.cache_hits, 0)
            self.assertEqual(second.cache_hits, len(second.chunks))
            self.assertEqual(second.extracted_chunks, 0)
            self.assertEqual(len(dsh.calls), first_call_count)
            self.assertEqual(len(first.facts), len(first.chunks))
            self.assertTrue(
                all(report.task_kind == "chapter_chunk_facts" for _, _, report in dsh.calls)
            )
            self.assertEqual(json.dumps(project.load_story_state(), sort_keys=True), old_state)
            self.assertEqual(
                json.dumps(project.load_chapter_summaries(), sort_keys=True),
                old_summaries,
            )

    def test_cancelled_workflow_makes_no_model_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = NovelProject.create(root / "project", "测试")
            project.save_chapter("chapter_01", outline="", content="正文。")
            dsh = _LedgerDSH()
            event = threading.Event()
            event.set()
            workflow = AIWorkflowService(dsh, fact_cache_root=root / "cache")

            with self.assertRaises(AITaskCancelled):
                workflow.build_chapter_fact_ledger(
                    project,
                    "chapter_01",
                    cancel_event=event,
                )
            self.assertEqual(dsh.calls, [])
