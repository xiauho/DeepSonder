"""Memory admission, convergence and deadline contracts without live AI."""
import json
import re
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from core.ai_workflow import AIWorkflowService
from core.chapter_facts import ChapterFactLedger, FactRecord, build_chunk_facts_prompt, memory_chunks
from core.chapter_memory import (ChapterMemoryCache, MemoryProposalBudgetError,
    MemoryProposalError, generate_chapter_memory_proposal)
from core.config import normalize_config
from core.dsh_client import DSHClient
from core.prompt_transport import estimate_task_input_tokens
from core.project import NovelProject
from core.task_controller import AITaskCancelled
from core.text_chunking import chunk_chapter
from core.token_budget import DEFAULT_TOKEN_ESTIMATOR as EST, TokenBudget
from tests.test_chapter_memory import _MemoryV2DSH, base_state, make_ledger, wire_id, wire_refs
from tests.test_dsh_client import current_task_ack


def snapshot(project):
    return {str(p.relative_to(project.root)): p.read_bytes()
            for p in project.root.rglob("*") if p.is_file()}


def dense_ledger():
    return ChapterFactLedger("chapter_01", "synthetic-hash", (), tuple(
        FactRecord(f"fact_{i:04d}", "character_state", f"角色{i}", "状态", "字" * 240,
            "chapter_01:p0001", "explicit") for i in range(20)), (), 0, 0)


class MemoryBudgetPolicyTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = NovelProject.create(self.root / "project", "临时记忆测试")
        self.project.save_chapter("chapter_01", outline="", content="林舟抵达北港。")
        self.project.save_story_state(base_state())
        self.cache = ChapterMemoryCache(self.project, self.root / "memory")

    def test_estimate_covers_actual_envelope_at_multiline_and_midword_boundaries(self):
        client = DSHClient()
        self.addCleanup(client.cleanup)
        for system, user in [("系统", "正文\n\n第二段。"), ("", "word" * 600),
                             ("只输出 JSON。", '{"text":"' + "字" * 1000 + '"}')]:
            with self.subTest(system=system):
                task = client._write_task_file(client._combine_prompts(system, user))
                actual = EST.estimate(task.path.read_text(encoding="utf-8")) + 128
                self.assertEqual(estimate_task_input_tokens(system, user), actual)
                client._remove_task_file(task)

    def test_builder_and_sender_agree_at_exact_admission_boundary(self):
        chunk = chunk_chapter("chapter_01", "林舟抵达北港。" + "字" * 900)[0]
        prompt = build_chunk_facts_prompt("第一章", chunk)
        budget = prompt.report.estimated_input_tokens
        build_chunk_facts_prompt("第一章", chunk, input_token_budget=budget)
        with self.assertRaisesRegex(RuntimeError, "超过 token 预算"):
            build_chunk_facts_prompt("第一章", chunk, input_token_budget=budget - 1)
        reports = []
        client = DSHClient(input_token_budget=budget, report_callback=reports.append)
        self.addCleanup(client.cleanup)
        client._file_transport_supported = True
        def execute(*_args, **_kwargs):
            return current_task_ack(client) + "\n完成"
        with patch.object(client, "_execute_prompt", side_effect=execute):
            self.assertEqual(client.generate(prompt.system_prompt, prompt.user_prompt,
                context_report=prompt.report), "完成")
        self.assertEqual(reports[0].estimated_input_tokens, budget)

    def test_low_budget_splits_all_text_instead_of_late_sender_rejection(self):
        text = "林舟抵达北港。" + "字" * 11000
        chunks = memory_chunks("chapter_01", text, "第一章", chunk_token_budget=5000,
            overlap_tokens=0, input_token_budget=4000, estimator=EST)
        self.assertGreater(len(chunks), len(chunk_chapter("chapter_01", text, overlap_tokens=0)))
        self.assertEqual("".join(c.text for c in chunks), text)
        for chunk in chunks:
            prompt = build_chunk_facts_prompt("第一章", chunk, input_token_budget=4000)
            self.assertLessEqual(prompt.report.estimated_input_tokens, 4000)

    def test_model_window_and_reserve_constrain_workflow_before_any_request(self):
        dsh = _MemoryV2DSH()
        dsh.token_budget = TokenBudget(input_limit=24000, runtime_reserve=2000,
                                      model_context_window=2500)
        workflow = AIWorkflowService(dsh, input_token_budget=24000,
            fact_cache_root=self.root / "facts", memory_cache_root=self.root / "memory")
        self.assertEqual(workflow.input_token_budget, 500)
        before = snapshot(self.project)
        with self.assertRaisesRegex(RuntimeError, "协议与传输包装"):
            workflow.update_memory(self.project, "chapter_01")
        self.assertEqual(dsh.calls, [])
        self.assertEqual(snapshot(self.project), before)

    def test_fixed_context_overflow_fails_before_any_reduction(self):
        dsh = _MemoryV2DSH()
        before = snapshot(self.project)
        with self.assertRaisesRegex(MemoryProposalBudgetError, "必要设定"):
            generate_chapter_memory_proposal(self.project, dense_ledger(), dsh,
                base_state=base_state(), canon_context="必要规则。" * 3000,
                input_token_budget=4000, cache=self.cache)
        self.assertEqual(dsh.calls, [])
        self.assertEqual(snapshot(self.project), before)
        self.assertFalse(list(self.cache.project_dir.rglob("*.json")))

    def test_selected_canon_is_never_cut_to_twelve_thousand_characters(self):
        canon = "必须遵守的设定。" * 2000 + "末尾关键约束：角色不能瞬移。"
        dsh = _MemoryV2DSH()
        ledger = make_ledger()
        ledger = replace(ledger, facts=(replace(ledger.facts[0], fact_id="fact_abcd"),))
        proposal = generate_chapter_memory_proposal(self.project, ledger, dsh,
            base_state=base_state(), canon_context=canon, input_token_budget=100000,
            cache=self.cache)
        self.assertFalse(proposal.has_blockers)
        self.assertEqual(len(dsh.calls), 1)
        self.assertIn(canon, dsh.calls[0])

    def test_missing_reduction_evidence_is_rejected_before_proposal_or_cache(self):
        calls = []
        def incomplete(_system, user, **_options):
            calls.append(user)
            ids = wire_refs(user)
            return {"type": "chapter_digest_shard", "schema_version": 1,
                "wire_request_id": wire_id(user),
                "summary": "人物变化", "claims": [{"text": "状态变化", "fact_ids": ids[:1]}]}
        dsh = Mock(generate_json=incomplete)
        with self.assertRaisesRegex(MemoryProposalError, "遗漏来源证据"):
            generate_chapter_memory_proposal(self.project, dense_ledger(), dsh,
                base_state=base_state(), input_token_budget=4000,
                reduce_batch_tokens=2000, cache=self.cache)
        self.assertEqual(len(calls), 1)
        self.assertFalse(list(self.cache.project_dir.rglob("*.json")))

    def test_non_converging_round_stops_without_repeated_rounds(self):
        calls, events = [], []
        def growing(_system, user, **_options):
            calls.append(user)
            self.assertIn("任务类型：chapter_digest_shard", user)
            ids = wire_refs(user)
            return {"type": "chapter_digest_shard", "schema_version": 1,
                "wire_request_id": wire_id(user),
                "summary": "字" * 1500, "claims": [{"text": "字" * 400, "fact_ids": ids}]}
        before = snapshot(self.project)
        with self.assertRaisesRegex(MemoryProposalBudgetError, "未有效收敛"):
            generate_chapter_memory_proposal(self.project, dense_ledger(),
                Mock(generate_json=growing), base_state=base_state(), input_token_budget=4000,
                reduce_batch_tokens=1000, cache=self.cache, progress_callback=events.append)
        self.assertEqual(max(e.round_number for e in events), 1)
        self.assertFalse(list(self.cache.project_dir.rglob("reduction-*.json")))
        self.assertFalse([p for p in self.cache.project_dir.rglob("*.json") if not p.name.startswith("batch-")])
        self.assertEqual(snapshot(self.project), before)

    def test_incomplete_reduction_cache_is_not_reused(self):
        ledger = dense_ledger()
        self.cache.save_reduction(ledger, {"mode": "digest_shards", "shards": [{
            "type": "chapter_digest_shard", "schema_version": 1, "summary": "摘要",
            "claims": [{"text": "状态", "fact_ids": [ledger.facts[0].fact_id]}]}]})
        self.assertIsNone(self.cache.load_reduction(ledger))

    def test_timeout_configuration_is_bounded_and_defaults_are_conservative(self):
        for value, expected in [(None, 1800), ("invalid", 1800), (-1, 30), (99999, 7200)]:
            with self.subTest(value=value):
                self.assertEqual(normalize_config({"ai_memory_timeout": value})["ai_memory_timeout"], expected)

    def test_expired_late_fact_is_not_cached_and_run_summary_is_redacted(self):
        clock, events = [0.0], []
        dsh = _MemoryV2DSH()
        original = dsh.generate_json
        def late(*args, **kwargs):
            result = original(*args, **kwargs)
            clock[0] = 31.0
            return result
        dsh.generate_json = late
        workflow = AIWorkflowService(dsh, memory_timeout=30,
            fact_cache_root=self.root / "facts", memory_cache_root=self.root / "memory",
            progress_callback=events.append)
        before = snapshot(self.project)
        with patch("core.memory_task.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(RuntimeError, "整次记忆更新等待超时"):
                workflow.update_memory(self.project, "chapter_01")
        self.assertEqual(len(dsh.calls), 1)
        self.assertFalse(list((self.root / "facts").rglob("*.json")))
        self.assertEqual((events[-1].stage, events[-1].state), ("summary", "timeout"))
        self.assertEqual(events[-1].request_count, 1)
        self.assertNotIn("林舟", json.dumps([e.__dict__ for e in events], ensure_ascii=False))
        self.assertEqual(snapshot(self.project), before)

    def test_retry_reuses_completed_facts_after_task_timeout(self):
        clock, events = [0.0], []
        content = "林舟抵达北港。" + "字" * 11000
        self.project.save_chapter("chapter_01", outline="", content=content)
        dsh = _MemoryV2DSH()
        original = dsh.generate_json
        def late_second(*args, **kwargs):
            result = original(*args, **kwargs)
            if len(dsh.calls) == 2:
                clock[0] = 31.0
            return result
        dsh.generate_json = late_second
        workflow = AIWorkflowService(dsh, memory_timeout=30,
            fact_cache_root=self.root / "facts", memory_cache_root=self.root / "memory",
            progress_callback=events.append)
        before = snapshot(self.project)
        with patch("core.memory_task.monotonic", side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(RuntimeError, "整次记忆更新等待超时"):
                workflow.update_memory(self.project, "chapter_01")
            self.assertEqual(len(list((self.root / "facts").rglob("*.json"))), 1)
            workflow.update_memory(self.project, "chapter_01")
        self.assertEqual(events[-1].state, "done")
        self.assertEqual(events[-1].cache_hits, 1)
        summaries = [e for e in events if e.stage == "summary"]
        self.assertNotEqual(summaries[0].run_id, summaries[1].run_id)
        self.assertEqual(snapshot(self.project), before)


class MemoryTransportDeadlineTests(TestCase):
    def setUp(self):
        self.reports = []
        self.client = DSHClient(timeout=600, report_callback=self.reports.append)
        self.addCleanup(self.client.cleanup)
        self.report = build_chunk_facts_prompt("章节", chunk_chapter("chapter_01", "正文。")[0]).report
        self.clock = [0.0]
        timer = patch("core.dsh_client.time.monotonic", side_effect=lambda: self.clock[0])
        timer.start()
        self.addCleanup(timer.stop)

    def test_probe_generation_and_receipt_retry_share_remaining_time(self):
        timeouts = []
        def probe(**options):
            self.assertEqual(options["timeout"], 5)
            self.clock[0] += 2
            return True
        def execute(_prompt, **options):
            timeouts.append(options["timeout"])
            self.clock[0] += 1
            return "缺少回执" if len(timeouts) == 1 else current_task_ack(self.client) + "\n完成"
        with patch.object(self.client, "_ensure_file_transport_support", side_effect=probe), \
             patch.object(self.client, "_execute_prompt", side_effect=execute):
            self.assertEqual(self.client.generate("系统", "任务", task_deadline=5,
                context_report=self.report), "完成")
        self.assertEqual(timeouts, [3, 2])
        self.assertEqual(self.reports[0].file_ack_retry_count, 1)
        self.assertEqual(self.reports[0].probe_count, 1)
        self.assertTrue(self.reports[0].task_file_cleaned)

    def test_probe_expiration_stops_before_business_generation(self):
        def probe(**_options):
            self.clock[0] = 6
            return True
        with patch.object(self.client, "_ensure_file_transport_support", side_effect=probe), \
             patch.object(self.client, "_execute_prompt") as execute:
            with self.assertRaisesRegex(RuntimeError, "整次记忆更新等待超时"):
                self.client.generate("系统", "任务", task_deadline=5, context_report=self.report)
        execute.assert_not_called()
        self.assertEqual(self.reports[0].outcome, "timeout")
        self.assertTrue(self.reports[0].task_file_cleaned)

    def test_expiration_after_missing_receipt_does_not_launch_retry(self):
        self.client._file_transport_supported = True
        def execute(*_args, **_options):
            self.clock[0] = 6
            return "缺少回执"
        with patch.object(self.client, "_execute_prompt", side_effect=execute) as run:
            with self.assertRaisesRegex(RuntimeError, "整次记忆更新等待超时"):
                self.client.generate("系统", "任务", task_deadline=5, context_report=self.report)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.reports[0].file_ack_retry_count, 0)
        self.assertEqual(self.reports[0].outcome, "timeout")
        self.assertTrue(self.reports[0].task_file_cleaned)

    def test_cancel_takes_precedence_over_expired_deadline(self):
        event = threading.Event()
        event.set()
        with patch.object(self.client, "_execute_prompt") as execute:
            with self.assertRaises(AITaskCancelled):
                self.client.generate("系统", "任务", task_deadline=0,
                    cancel_event=event, context_report=self.report)
        execute.assert_not_called()
        self.assertEqual(self.reports[0].outcome, "cancelled")

    def test_report_keeps_run_and_request_identity_without_task_text(self):
        self.client._file_transport_supported = True
        report = replace(self.report, memory_run_id="run-123", request_index=2,
                         source_unit="chapter_01:c0002")
        def execute(*_args, **_options):
            return current_task_ack(self.client) + '\n{"ok":true}'
        with patch.object(self.client, "_execute_prompt", side_effect=execute):
            self.client.generate_json("系统", "私密正文", task_deadline=5, context_report=report)
        result = self.reports[0]
        self.assertEqual((result.memory_run_id, result.request_index, result.source_unit),
                         ("run-123", 2, "chapter_01:c0002"))
        self.assertNotIn("私密正文", json.dumps(result.to_dict(), ensure_ascii=False))
