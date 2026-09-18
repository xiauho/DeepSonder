"""Deterministic memory performance contracts; no live model calls."""
import json
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core.ai_workflow import AIWorkflowService
from core.chapter_facts import memory_chunks, build_chunk_facts_prompt
from core.memory_progress import memory_phase
from core.project import NovelProject
from core.task_controller import AITaskCancelled
from core.text_chunking import chunk_chapter
from core.token_budget import DEFAULT_TOKEN_ESTIMATOR
from tests.test_chapter_facts import _LedgerDSH
from tests.test_chapter_memory import _MemoryV2DSH, base_state


class MemoryPerformanceTests(TestCase):
    def chunks(self, text, chunk_budget=5000, input_budget=24000):
        return memory_chunks("chapter_01", text, "第一章",
            chunk_token_budget=chunk_budget, overlap_tokens=300,
            input_token_budget=input_budget, estimator=DEFAULT_TOKEN_ESTIMATOR)

    def test_medium_chapter_reduces_requests_without_losing_text_or_anchors(self):
        text = "林舟抵达北港。" + "字" * 5990
        original = chunk_chapter("chapter_01", text)
        adapted = self.chunks(text)
        self.assertGreater(len(original), 1)
        self.assertEqual(len(adapted), 1)
        self.assertEqual(adapted[0].text, text)
        self.assertEqual(adapted[0].start_paragraph, 1)
        prompt = build_chunk_facts_prompt("第一章", adapted[0])
        self.assertLessEqual(prompt.report.estimated_input_tokens + 512, 24000)

    def test_custom_budget_low_input_and_long_chapters_keep_bounded_chunks(self):
        for text, chunk_budget, input_budget in (
            ("字" * 6000, 2000, 24000),
            ("字" * 6000, 5000, 10000),
            ("字" * 12000, 5000, 24000),
        ):
            with self.subTest(chunk_budget=chunk_budget, input_budget=input_budget, length=len(text)):
                expected = chunk_chapter("chapter_01", text, target_tokens=chunk_budget)
                self.assertEqual(self.chunks(text, chunk_budget, input_budget), expected)

    def test_exact_prompt_budget_can_reject_adaptive_candidate(self):
        text = "字" * 6000
        with patch("core.chapter_facts.build_chunk_facts_prompt", side_effect=RuntimeError("budget")):
            self.assertEqual(self.chunks(text), chunk_chapter("chapter_01", text))

    def test_progress_cache_reuse_and_no_project_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = NovelProject.create(root / "project", "测试")
            project.save_chapter("chapter_01", outline="", content="林舟抵达北港。")
            project.save_story_state(base_state())
            before = {str(p.relative_to(project.root)): p.read_bytes()
                      for p in project.root.rglob("*") if p.is_file()}
            events = []
            dsh = _MemoryV2DSH()
            workflow = AIWorkflowService(dsh, fact_cache_root=root / "facts",
                memory_cache_root=root / "memory", progress_callback=events.append)
            workflow.update_memory(project, "chapter_01")
            self.assertEqual(len(dsh.calls), 2)
            stages = [e.stage for e in events if e.state == "done"]
            self.assertEqual(stages, ["chunking", "facts", "merge", "context", "proposal", "validation"])
            self.assertTrue(all(e.elapsed_ms >= 0 for e in events))
            self.assertNotIn("林舟", json.dumps([asdict(e) for e in events], ensure_ascii=False))
            events.clear()
            result = workflow.update_memory(project, "chapter_01")
            self.assertTrue(result.cache_hit)
            self.assertEqual(len(dsh.calls), 2)
            self.assertEqual([e.stage for e in events if e.state == "cached"], ["facts", "proposal"])
            after = {str(p.relative_to(project.root)): p.read_bytes()
                     for p in project.root.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_cancel_during_extraction_does_not_cache_or_continue(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = NovelProject.create(root / "project", "测试")
            project.save_chapter("chapter_01", outline="", content="正文。")
            event = threading.Event()
            events = []
            fake = _LedgerDSH()
            generate = fake.generate_json
            def cancel_after_response(*args, **kwargs):
                result = generate(*args, **kwargs)
                event.set()
                return result
            fake.generate_json = cancel_after_response
            workflow = AIWorkflowService(fake, fact_cache_root=root / "cache", progress_callback=events.append)
            with self.assertRaises(AITaskCancelled):
                workflow.update_memory(project, "chapter_01", cancel_event=event)
            self.assertEqual(len(fake.calls), 1)
            self.assertFalse(list((root / "cache").rglob("*.json")))
            self.assertEqual(events[-1].state, "interrupted")

    def test_phase_times_failure_and_ignores_display_failure(self):
        events = []
        with patch("core.memory_progress.perf_counter", side_effect=[10, 10.25]):
            with self.assertRaisesRegex(ValueError, "invalid"):
                with memory_phase(events.append, "validation"):
                    raise ValueError("invalid")
        self.assertEqual(events[-1].elapsed_ms, 250)
        self.assertEqual(events[-1].state, "interrupted")
        def broken_ui(event):
            raise RuntimeError("display closed")
        with memory_phase(broken_ui, "validation"):
            pass
