"""Request-local evidence and resumable reduction; temporary projects only."""
import copy
import json
import re
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from core.chapter_facts import UnknownRecord
from core.chapter_memory import (ChapterMemoryCache, MEMORY_PROPOSAL_PROMPT_VERSION,
    MemoryProposalError, canonical_hash, generate_chapter_memory_proposal,
    build_memory_proposal_prompt, _ledger_payload)
from core.memory_task import MemoryTaskSession
from core.memory_wire import MemoryWireError, encode_memory_source
from core.project import NovelProject
from core.task_controller import AITaskCancelled
from core.token_budget import DEFAULT_TOKEN_ESTIMATOR as EST
from tests.test_chapter_memory import base_state, make_ledger, wire_id, wire_refs
from tests.test_memory_budget_policy import dense_ledger, snapshot


class ReducingDSH:
    profile = "headless"
    extra_args = []

    def __init__(self, fail_batch=0, fail_proposal=False, after_response=None):
        self.calls = []
        self.batches = 0
        self.fail_batch = fail_batch
        self.fail_proposal = fail_proposal
        self.after_response = after_response

    def generate_json(self, _system, user, **_options):
        self.calls.append(user)
        ids = wire_refs(user)
        if "任务类型：chapter_digest_shard" in user:
            self.batches += 1
            if self.batches == self.fail_batch:
                raise RuntimeError("模拟归并中断")
            result = {"type": "chapter_digest_shard", "schema_version": 1,
                "wire_request_id": wire_id(user), "summary": "本批人物状态变化。",
                "claims": [{"text": "状态变化", "fact_ids": ids[i:i + 40]}
                           for i in range(0, len(ids), 40)]}
        else:
            if self.fail_proposal:
                raise RuntimeError("模拟提案中断")
            result = {"type": "chapter_memory_suggestion", "schema_version": 2,
                "request_id": re.search(r"^请求 ID：(.*)$", user, re.MULTILINE).group(1),
                "wire_request_id": wire_id(user),
                "digest": {"summary": "本章人物状态变化。", "key_events": [
                    {"text": "状态变化", "fact_ids": ids[:1]}]}, "changes": [], "conflicts": []}
        if self.after_response:
            self.after_response(self, result)
        return result


class MemoryWireTests(TestCase):
    def encode(self, source):
        return encode_memory_source(source, prompt_version=MEMORY_PROPOSAL_PROMPT_VERSION)

    def test_compact_rows_retain_every_fact_field_order_anchor_and_unknown(self):
        ledger = replace(make_ledger(), unknowns=(UnknownRecord("去向未明", "chapter_01:p0002"),))
        source = _ledger_payload(ledger)
        original = copy.deepcopy(source)
        wire = self.encode(source)
        fact = ledger.facts[0]
        self.assertEqual(wire.value["facts"][0], ["f1", fact.category, fact.subject,
            fact.predicate, fact.value, fact.anchor, fact.certainty])
        self.assertEqual(wire.value["unknowns"], [["去向未明", "chapter_01:p0002"]])
        self.assertEqual(wire.value["chunks"], [[ledger.chunks[0].chunk_id, ledger.chunks[0].chunk_summary]])
        self.assertEqual(source, original)
        self.assertEqual(wire.aliases, {"f1": fact.fact_id})

    def test_only_evidence_fields_are_decoded_even_when_text_looks_like_an_alias(self):
        wire = self.encode(_ledger_payload(make_ledger()))
        response = {"wire_request_id": wire.wire_request_id, "digest": {
            "key_events": [{"text": "f1", "fact_ids": ["f1"]}]},
            "changes": [{"value": "f1", "evidence_fact_ids": ["f1"]}],
            "conflicts": [{"evidence_fact_ids": ["f1"]}]}
        decoded = wire.decode(response)
        self.assertEqual(decoded["digest"]["key_events"][0],
            {"text": "f1", "fact_ids": ["fact_location"]})
        self.assertEqual(decoded["changes"][0]["value"], "f1")
        self.assertEqual(decoded["conflicts"][0]["evidence_fact_ids"], ["fact_location"])
        self.assertEqual(response["changes"][0]["evidence_fact_ids"], ["f1"])

    def test_cross_request_unknown_full_id_and_malformed_references_are_rejected(self):
        wire = self.encode(_ledger_payload(make_ledger()))
        changed = self.encode(_ledger_payload(replace(make_ledger(), facts=(
            replace(make_ledger().facts[0], value="南城"),))))
        with self.assertRaisesRegex(MemoryWireError, "wire_request_id"):
            wire.decode({"wire_request_id": changed.wire_request_id, "fact_ids": ["f1"]})
        for refs in (["f99"], ["fact_location"], [1], "f1", None):
            with self.subTest(refs=refs), self.assertRaisesRegex(MemoryWireError, "短引用"):
                wire.decode({"wire_request_id": wire.wire_request_id, "fact_ids": refs})

    def test_projection_reduces_estimated_payload_without_cropping_values(self):
        fact = make_ledger().facts[0]
        ledger = replace(make_ledger(), chunks=(), facts=tuple(
            replace(fact, fact_id="fact_" + f"{i:064x}", value=f"保留地点{i}",
                    anchor=f"chapter_01:p{i + 1:04d}") for i in range(100)))
        source = _ledger_payload(ledger)
        wire = self.encode(source)
        render = lambda item: json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        self.assertLess(EST.estimate(render(wire.value)), EST.estimate(render(source)) * .8)
        self.assertEqual(len(wire.value["facts"]), 100)
        self.assertEqual(wire.value["facts"][-1][4:6], ["保留地点99", "chapter_01:p0100"])

    def test_compact_prompt_uses_exact_full_transport_budget(self):
        ledger, state = make_ledger(), base_state()
        source = {"mode": "fact_ledger", "base_state_hash": canonical_hash(state), **_ledger_payload(ledger)}
        prompt = build_memory_proposal_prompt(ledger, source, state, "", context_hash="ctx")
        self.assertNotIn("fact_location", prompt.user_prompt)
        self.assertIn(ledger.facts[0].anchor, prompt.user_prompt)
        exact = prompt.report.estimated_input_tokens
        build_memory_proposal_prompt(ledger, source, state, "", context_hash="ctx", input_token_budget=exact)
        with self.assertRaisesRegex(RuntimeError, "超过 token 预算"):
            build_memory_proposal_prompt(ledger, source, state, "", context_hash="ctx", input_token_budget=exact - 1)

    def test_later_round_remaps_shared_evidence_without_rewriting_claim_text(self):
        source = [{"summary": "片段", "claims": [
            {"text": "f2 到达后才发生变化", "fact_ids": ["fact_b", "fact_a"]},
            {"text": "未解决问题", "fact_ids": ["fact_a"]}]}]
        wire = self.encode(source)
        self.assertEqual(wire.aliases, {"f1": "fact_b", "f2": "fact_a"})
        self.assertEqual(wire.value[0]["claims"][0]["fact_ids"], ["f1", "f2"])
        self.assertEqual(wire.value[0]["claims"][0]["text"], source[0]["claims"][0]["text"])
        reordered = self.encode([{**source[0], "claims": list(reversed(source[0]["claims"]))}])
        self.assertNotEqual(wire.wire_request_id, reordered.wire_request_id)


class MemoryCheckpointTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = NovelProject.create(self.root / "project", "临时归并检查点测试")
        self.project.save_chapter("chapter_01", outline="", content="临时正文。")
        self.cache = ChapterMemoryCache(self.project, self.root / "cache")
        self.ledger = dense_ledger()
        self.before = snapshot(self.project)

    def run_memory(self, dsh, **options):
        return generate_chapter_memory_proposal(self.project, options.pop("ledger", self.ledger), dsh,
            base_state=options.pop("base_state", base_state()), input_token_budget=4000,
            reduce_batch_tokens=1000, cache=self.cache, **options)

    def interrupt(self):
        dsh = ReducingDSH(fail_batch=2)
        with self.assertRaisesRegex(RuntimeError, "归并中断"):
            self.run_memory(dsh)
        self.assertEqual(len(list(self.cache.project_dir.rglob("batch-*.json"))), 1)
        return dsh

    def test_failure_resume_calls_only_missing_batches_and_preserves_project(self):
        first = self.interrupt()
        events, resumed = [], ReducingDSH()
        proposal = self.run_memory(resumed, progress_callback=events.append)
        self.assertNotIn(first.calls[0], resumed.calls)
        self.assertEqual(resumed.calls[0], first.calls[1])
        hits = [e for e in events if e.cache_reason == "batch_hit"]
        self.assertEqual(len(hits), 1)
        self.assertEqual((hits[0].current, hits[0].round_number), (1, 1))
        self.assertEqual(proposal.reduction_calls, resumed.batches)
        self.assertEqual(snapshot(self.project), self.before)
        cached = "".join(p.read_text("utf-8") for p in self.cache.project_dir.rglob("batch-*.json"))
        self.assertIn("fact_0000", cached)
        self.assertNotIn('"fact_ids": ["f1"]', cached)

    def test_cancel_and_timeout_preserve_prior_batch_but_never_save_late_batch(self):
        for kind in ("cancel", "timeout"):
            with self.subTest(kind=kind):
                cache = ChapterMemoryCache(self.project, self.root / kind)
                self.cache = cache
                cancel, clock = threading.Event(), [0.0]
                def late(dsh, _result):
                    if dsh.batches == 2:
                        if kind == "cancel":
                            cancel.set()
                        else:
                            clock[0] = 31.0
                fake = ReducingDSH(after_response=late)
                with patch("core.memory_task.monotonic", side_effect=lambda: clock[0]):
                    session = MemoryTaskSession(fake, 30, cancel, None)
                    with self.assertRaises(AITaskCancelled if kind == "cancel" else RuntimeError):
                        self.run_memory(session, cancel_event=cancel)
                self.assertEqual(len(list(cache.project_dir.rglob("batch-*.json"))), 1)
                resumed = ReducingDSH()
                self.run_memory(resumed)
                self.assertEqual(resumed.calls[0], fake.calls[1])
        self.assertEqual(snapshot(self.project), self.before)

    def test_corrupt_or_incomplete_checkpoint_is_recomputed(self):
        first = self.interrupt()
        path = next(self.cache.project_dir.rglob("batch-*.json"))
        original = path.read_text("utf-8")
        for variant in ("json", "result_hash", "missing_evidence", "unknown_evidence"):
            with self.subTest(variant=variant):
                value = json.loads(original)
                if variant == "result_hash":
                    value["result_hash"] = "invalid"
                elif variant in {"missing_evidence", "unknown_evidence"}:
                    refs = value["shard"]["claims"][0]["fact_ids"]
                    self.assertGreater(len(refs), 1)
                    value["shard"]["claims"][0]["fact_ids"] = refs[:1] if variant == "missing_evidence" else ["fact_other"]
                    value["result_hash"] = canonical_hash(value["shard"])
                path.write_text("{" if variant == "json" else json.dumps(value), encoding="utf-8")
                fake = ReducingDSH(fail_batch=2)
                with self.assertRaises(RuntimeError):
                    self.run_memory(fake)
                self.assertEqual(fake.calls[0], first.calls[0])

    def test_batch_identity_binds_order_contents_protocol_and_model_configuration(self):
        self.interrupt()
        variants = [
            {"ledger": replace(self.ledger, facts=tuple(reversed(self.ledger.facts)))},
            {"ledger": replace(self.ledger, facts=(replace(self.ledger.facts[0], value="另一个状态"), *self.ledger.facts[1:]))},
            {"ledger": replace(self.ledger, facts=(replace(self.ledger.facts[0], fact_id="fact_different"), *self.ledger.facts[1:]))},
        ]
        for options in variants:
            fake, events = ReducingDSH(fail_batch=2), []
            with self.assertRaisesRegex(RuntimeError, "归并中断"):
                self.run_memory(fake, progress_callback=events.append, **options)
            self.assertEqual(fake.batches, 2)
            self.assertFalse(any(e.cache_reason == "batch_hit" for e in events))
        changed = ReducingDSH(fail_batch=2)
        changed.extra_args = ["--model", "different-model"]
        with self.assertRaises(RuntimeError):
            self.run_memory(changed)
        self.assertEqual(changed.batches, 2)
        with patch("core.chapter_memory.MEMORY_PROPOSAL_PROMPT_VERSION", 999):
            other = ReducingDSH(fail_batch=2)
            with self.assertRaises(RuntimeError):
                self.run_memory(other)
            self.assertEqual(other.batches, 2)

    def test_refresh_bypasses_every_batch_checkpoint(self):
        first = self.interrupt()
        fake = ReducingDSH(fail_batch=2)
        with self.assertRaises(RuntimeError):
            self.run_memory(fake, refresh_reduction=True)
        self.assertEqual(fake.calls[0], first.calls[0])

    def test_state_or_canon_change_reuses_context_independent_completed_batch(self):
        first = self.interrupt()
        fake, events = ReducingDSH(), []
        self.run_memory(fake, base_state={"current_chapter": 1, "characters": {}},
            canon_context="角色不得瞬移。", progress_callback=events.append)
        self.assertNotIn(first.calls[0], fake.calls)
        self.assertEqual(sum(e.cache_reason == "batch_hit" for e in events), 1)
        self.assertIn("角色不得瞬移。", fake.calls[-1])

    def test_all_completed_batches_can_rebuild_missing_aggregate_without_ai_reduction(self):
        first = ReducingDSH(fail_proposal=True)
        with self.assertRaisesRegex(RuntimeError, "提案中断"):
            self.run_memory(first)
        fake, events = ReducingDSH(), []
        with patch.object(self.cache, "load_reduction", return_value=None):
            result = self.run_memory(fake, progress_callback=events.append)
        self.assertEqual((fake.batches, result.reduction_calls, len(fake.calls)), (0, 0, 1))
        self.assertEqual(sum(e.cache_reason == "batch_hit" for e in events), first.batches)

    def test_bad_wire_response_never_creates_a_checkpoint(self):
        def wrong(_dsh, response):
            response["wire_request_id"] = "wire_other_request"
        with self.assertRaisesRegex(MemoryProposalError, "wire_request_id"):
            self.run_memory(ReducingDSH(after_response=wrong))
        self.assertFalse(list(self.cache.project_dir.rglob("*.json")))

    def test_unknown_final_reference_never_creates_proposal_cache(self):
        def wrong(_dsh, response):
            response["digest"]["key_events"][0]["fact_ids"] = ["f999"]
        with self.assertRaisesRegex(MemoryProposalError, "不存在的事实短引用"):
            self.run_memory(ReducingDSH(after_response=wrong), ledger=make_ledger())
        self.assertFalse(list(self.cache.project_dir.rglob("*.json")))

    def test_expiration_while_loading_proposal_cache_does_not_return_it_for_review(self):
        from tests.test_chapter_memory import _MemoryV2DSH
        self.run_memory(_MemoryV2DSH(), ledger=make_ledger())
        clock, fake = [0.0], _MemoryV2DSH()
        original = self.cache.load
        def slow_load(*args):
            result = original(*args)
            self.assertIsNotNone(result)
            clock[0] = 31.0
            return result
        with patch("core.memory_task.monotonic", side_effect=lambda: clock[0]), \
             patch.object(self.cache, "load", side_effect=slow_load):
            session = MemoryTaskSession(fake, 30, None, None)
            with self.assertRaisesRegex(RuntimeError, "整次记忆更新等待超时"):
                self.run_memory(session, ledger=make_ledger())
        self.assertEqual(fake.calls, [])

    def test_proposal_cache_invalidates_same_ids_with_changed_fact_semantics(self):
        ledger = make_ledger()
        from tests.test_chapter_memory import _MemoryV2DSH
        first = _MemoryV2DSH()
        self.run_memory(first, ledger=ledger)
        changed = replace(ledger, facts=(replace(ledger.facts[0], certainty="inferred"),))
        fake = _MemoryV2DSH()
        result = self.run_memory(fake, ledger=changed)
        self.assertFalse(result.cache_hit)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("inferred_patch", {c.kind for c in result.conflicts})
