import json
import re
import tempfile
from pathlib import Path
from unittest import TestCase

from core.ai_result_service import AIResultService
from core.ai_workflow import AIWorkflowService
from core.chapter_facts import ChapterFactLedger, ChunkFacts, FactRecord
from core.chapter_memory import (
    ChapterMemoryCache,
    MemoryProposalError,
    build_memory_proposal_prompt,
    canonical_hash,
    generate_chapter_memory_proposal,
    memory_request_id,
    parse_memory_proposal,
)
from core.project import NovelProject
from core.text_chunking import chapter_content_hash
from core.token_budget import DEFAULT_TOKEN_ESTIMATOR


def make_ledger(chapter_hash: str = "chapter-hash") -> ChapterFactLedger:
    fact = FactRecord(
        "fact_location",
        "location",
        "林舟",
        "抵达",
        "北港",
        "chapter_01:p0001",
        "explicit",
    )
    chunk = ChunkFacts(
        chapter_id="chapter_01",
        chunk_id="chapter_01:c0001:p0001-p0001",
        source_hash="chunk-hash",
        chunk_summary="林舟抵达北港。",
        facts=(fact,),
        unknowns=(),
    )
    return ChapterFactLedger(
        chapter_id="chapter_01",
        chapter_hash=chapter_hash,
        chunks=(chunk,),
        facts=(fact,),
        unknowns=(),
        cache_hits=0,
        extracted_chunks=1,
    )


def base_state():
    return {
        "current_chapter": 1,
        "current_location": "旧站",
        "characters": {
            "林舟": {
                "location": "旧站",
                "items": ["钥匙"],
                "relations": {},
            }
        },
        "foreshadowing": ["作者维护的伏笔"],
    }


def valid_proposal(ledger, state, context_hash="context-hash"):
    return {
        "type": "chapter_memory_suggestion",
        "schema_version": 2,
        "request_id": memory_request_id(
            ledger.chapter_id,
            ledger.chapter_hash,
            canonical_hash(state),
            context_hash,
        ),
        "digest": {
            "summary": "林舟离开旧站并抵达北港。",
            "key_events": [
                {"text": "林舟抵达北港", "fact_ids": ["fact_location"]}
            ],
            "character_changes": [],
            "location_changes": [
                {"text": "林舟位置变为北港", "fact_ids": ["fact_location"]}
            ],
            "item_changes": [],
            "relationship_changes": [],
            "timeline_changes": [],
        },
        "changes": [
            {
                "kind": "set_character_field",
                "subject": "林舟",
                "field": "location",
                "value": "北港",
                "evidence_fact_ids": ["fact_location"],
                "certainty": "explicit",
            }
        ],
        "conflicts": [],
    }


class ChapterMemoryProtocolTests(TestCase):
    def test_memory_prompt_requests_only_one_authoritative_summary(self):
        ledger = make_ledger()
        state = base_state()
        prompt = build_memory_proposal_prompt(
            ledger,
            {
                "mode": "fact_ledger",
                "base_state_hash": canonical_hash(state),
            },
            state,
            "",
            context_hash="context-hash",
        )

        response_contract = prompt.user_prompt.split("返回格式：", 1)[1]
        self.assertIn("唯一权威字段是 digest.summary", prompt.user_prompt)
        self.assertEqual(response_contract.count('"summary": "章节摘要"'), 1)
        self.assertIn('"type": "chapter_memory_suggestion"', response_contract)
        self.assertNotIn('"chapter_hash"', response_contract)
        self.assertNotIn('"base_state_hash"', response_contract)
        self.assertNotIn('"context_hash"', response_contract)
        self.assertNotIn('"expected_before"', response_contract)
        self.assertNotIn('"completion_message"', response_contract)
        self.assertNotIn(ledger.chapter_hash, prompt.user_prompt)
        self.assertNotIn(canonical_hash(state), prompt.user_prompt)
        self.assertIn("严格表示本章开始前的状态", prompt.user_prompt)
        self.assertIn("不能只凭“一行人”“众人”", prompt.user_prompt)
        self.assertIn("同时引用地点事实", prompt.user_prompt)

    def test_v2_response_must_match_compact_request_id(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        value["request_id"] = "memory_wrong"

        with self.assertRaisesRegex(MemoryProposalError, "request_id"):
            parse_memory_proposal(
                value,
                ledger,
                state,
                expected_context_hash="context-hash",
            )

    def test_v2_ignores_duplicate_summary_and_completion_extras(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        value["summary"] = "模型额外返回的另一种摘要措辞。"
        value["completion_message"] = "模型自定义完成文案"

        proposal = parse_memory_proposal(
            value,
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertEqual(proposal.summary, "林舟离开旧站并抵达北港。")
        self.assertEqual(proposal.completion_message, "章节记忆提案已生成")
        self.assertNotIn("summary", proposal.to_cache_dict())

    def test_v1_model_response_is_rejected(self):
        ledger = make_ledger()
        state = base_state()
        value = {
            "type": "chapter_memory_proposal",
            "schema_version": 1,
        }

        with self.assertRaisesRegex(MemoryProposalError, "已停用"):
            parse_memory_proposal(
                value,
                ledger,
                state,
                expected_context_hash="context-hash",
            )

    def test_old_response_type_is_rejected_even_with_v2_version(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        value["type"] = "chapter_memory_proposal"

        with self.assertRaisesRegex(MemoryProposalError, "不是章节记忆建议"):
            parse_memory_proposal(
                value,
                ledger,
                state,
                expected_context_hash="context-hash",
            )

    def test_memory_proposal_still_requires_one_summary(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        del value["digest"]["summary"]

        with self.assertRaisesRegex(MemoryProposalError, "digest.summary"):
            parse_memory_proposal(
                value,
                ledger,
                state,
                expected_context_hash="context-hash",
            )

    def test_valid_patch_is_applied_locally_and_protected_fields_survive(self):
        ledger = make_ledger()
        state = base_state()
        proposal = parse_memory_proposal(
            valid_proposal(ledger, state),
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertFalse(proposal.has_blockers)
        self.assertEqual(
            proposal.resulting_state["characters"]["林舟"]["location"],
            "北港",
        )
        self.assertEqual(
            proposal.resulting_state["foreshadowing"],
            ["作者维护的伏笔"],
        )
        self.assertTrue(proposal.patches[0].op_id.startswith("op_"))

    def test_unknown_fact_reference_is_rejected(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        value["changes"][0]["evidence_fact_ids"] = ["fact_missing"]

        with self.assertRaisesRegex(MemoryProposalError, "不存在"):
            parse_memory_proposal(
                value,
                ledger,
                state,
                expected_context_hash="context-hash",
            )

    def test_unrelated_fact_cannot_authorize_character_patch(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        value["changes"][0]["kind"] = "set_character_relation"
        value["changes"][0]["field"] = "顾青"
        value["changes"][0]["value"] = "盟友"

        proposal = parse_memory_proposal(
            value,
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertTrue(proposal.has_blockers)
        self.assertIn("unsupported_patch", {item.kind for item in proposal.conflicts})

    def test_group_location_and_named_presence_can_support_character_location(self):
        ledger = make_ledger()
        group_location = FactRecord(
            "fact_group_location",
            "location",
            "一行人",
            "停下休整",
            "北港",
            "chapter_01:p0001",
            "explicit",
        )
        named_presence = FactRecord(
            "fact_named_presence",
            "event",
            "林舟",
            "随队到达",
            "与一行人共同抵达休整处",
            "chapter_01:p0001",
            "explicit",
        )
        ledger = ChapterFactLedger(
            chapter_id=ledger.chapter_id,
            chapter_hash=ledger.chapter_hash,
            chunks=ledger.chunks,
            facts=(group_location, named_presence),
            unknowns=(),
            cache_hits=0,
            extracted_chunks=1,
        )
        state = base_state()
        value = valid_proposal(ledger, state)
        value["digest"]["key_events"][0]["fact_ids"] = ["fact_group_location"]
        value["digest"]["location_changes"][0]["fact_ids"] = ["fact_group_location"]
        value["changes"][0]["evidence_fact_ids"] = [
            "fact_group_location",
            "fact_named_presence",
        ]

        proposal = parse_memory_proposal(
            value,
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertFalse(proposal.has_blockers)
        self.assertEqual(
            proposal.resulting_state["characters"]["林舟"]["location"],
            "北港",
        )

    def test_anonymous_group_location_alone_cannot_support_character_location(self):
        ledger = make_ledger()
        group_location = FactRecord(
            "fact_group_location",
            "location",
            "一行人",
            "停下休整",
            "北港",
            "chapter_01:p0001",
            "explicit",
        )
        ledger = ChapterFactLedger(
            chapter_id=ledger.chapter_id,
            chapter_hash=ledger.chapter_hash,
            chunks=ledger.chunks,
            facts=(group_location,),
            unknowns=(),
            cache_hits=0,
            extracted_chunks=1,
        )
        state = base_state()
        value = valid_proposal(ledger, state)
        value["digest"]["key_events"][0]["fact_ids"] = ["fact_group_location"]
        value["digest"]["location_changes"][0]["fact_ids"] = ["fact_group_location"]
        value["changes"][0]["evidence_fact_ids"] = ["fact_group_location"]

        proposal = parse_memory_proposal(
            value,
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertTrue(proposal.has_blockers)
        self.assertIn("unsupported_patch", {item.kind for item in proposal.conflicts})

    def test_model_precondition_is_ignored_and_local_value_is_authoritative(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        value["changes"][0]["expected_before"] = "错误地点"

        proposal = parse_memory_proposal(
            value,
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertFalse(proposal.has_blockers)
        self.assertEqual(proposal.patches[0].expected_before, "旧站")
        self.assertEqual(
            proposal.resulting_state["characters"]["林舟"]["location"],
            "北港",
        )

    def test_colliding_patch_targets_create_blocker(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        second = dict(value["changes"][0])
        second["value"] = "南城"
        value["changes"].append(second)

        proposal = parse_memory_proposal(
            value,
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertTrue(proposal.has_blockers)
        self.assertIn("patch_collision", {item.kind for item in proposal.conflicts})

    def test_inferred_patch_is_retained_with_warning(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        value["changes"][0]["certainty"] = "inferred"

        proposal = parse_memory_proposal(
            value,
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertFalse(proposal.has_blockers)
        self.assertIn("inferred_patch", {item.kind for item in proposal.conflicts})

    def test_conflicting_facts_at_same_anchor_create_blocker(self):
        ledger = make_ledger()
        conflicting = FactRecord(
            "fact_other_location",
            "location",
            "林舟",
            "抵达",
            "南城",
            "chapter_01:p0001",
            "explicit",
        )
        ledger = ChapterFactLedger(
            chapter_id=ledger.chapter_id,
            chapter_hash=ledger.chapter_hash,
            chunks=ledger.chunks,
            facts=(*ledger.facts, conflicting),
            unknowns=(),
            cache_hits=0,
            extracted_chunks=1,
        )
        state = base_state()
        proposal = parse_memory_proposal(
            valid_proposal(ledger, state),
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertTrue(proposal.has_blockers)
        self.assertIn("fact_collision", {item.kind for item in proposal.conflicts})


class ChapterMemoryCommitTests(TestCase):
    def test_commit_is_atomic_entry_and_rejects_stale_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "project", "测试")
            project.save_chapter(
                "chapter_01",
                outline="",
                content="林舟抵达北港。",
                title="第一章",
            )
            state = base_state()
            project.save_story_state(state)
            ledger = make_ledger(chapter_content_hash("林舟抵达北港。"))
            proposal = parse_memory_proposal(
                valid_proposal(ledger, state),
                ledger,
                state,
                expected_context_hash="context-hash",
            )

            result = AIResultService.commit_memory_proposal(
                project,
                "chapter_01",
                proposal,
            )
            self.assertEqual(
                result.merged_state["characters"]["林舟"]["location"],
                "北港",
            )
            self.assertEqual(
                project.load_chapter_summaries()["chapter_01"],
                proposal.summary,
            )

            stale_state = project.load_story_state()
            stale_state["current_location"] = "其他地点"
            project.save_story_state(stale_state)
            with self.assertRaisesRegex(ValueError, "故事状态"):
                AIResultService.commit_memory_proposal(
                    project,
                    "chapter_01",
                    proposal,
                )

    def test_blocking_proposal_cannot_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "project", "测试")
            content = "林舟抵达北港。"
            project.save_chapter("chapter_01", outline="", content=content)
            state = base_state()
            project.save_story_state(state)
            ledger = make_ledger(chapter_content_hash(content))
            value = valid_proposal(ledger, state)
            second = dict(value["changes"][0])
            second["value"] = "南城"
            value["changes"].append(second)
            proposal = parse_memory_proposal(
                value,
                ledger,
                state,
                expected_context_hash="context-hash",
            )

            with self.assertRaisesRegex(ValueError, "阻断冲突"):
                AIResultService.commit_memory_proposal(
                    project,
                    "chapter_01",
                    proposal,
                )


class _MemoryV2DSH:
    input_token_budget = 24_000
    token_estimator = DEFAULT_TOKEN_ESTIMATOR

    def __init__(self):
        self.calls = []

    def generate_json(self, system_prompt, user_prompt, **kwargs):
        self.calls.append(user_prompt)
        if "任务类型：chapter_chunk_facts" in user_prompt:
            chapter_id = re.search(r"^章节 ID：(.*)$", user_prompt, re.MULTILINE).group(1)
            chunk_id = re.search(r"^分块 ID：(.*)$", user_prompt, re.MULTILINE).group(1)
            source_hash = re.search(r"^来源哈希：(.*)$", user_prompt, re.MULTILINE).group(1)
            return {
                "type": "chapter_chunk_facts",
                "schema_version": 1,
                "chapter_id": chapter_id,
                "chunk_id": chunk_id,
                "source_hash": source_hash,
                "chunk_summary": "林舟抵达北港。",
                "facts": [
                    {
                        "category": "location",
                        "subject": "林舟",
                        "predicate": "抵达",
                        "value": "北港",
                        "anchor": f"{chapter_id}:p0001",
                        "certainty": "explicit",
                    }
                ],
                "unknowns": [],
            }
        if "任务类型：chapter_memory_suggestion" in user_prompt:
            fact_id = re.search(r'"fact_id":"(fact_[a-f0-9]+)"', user_prompt).group(1)
            request_id = re.search(r"^请求 ID：(.*)$", user_prompt, re.MULTILINE).group(1)
            return {
                "type": "chapter_memory_suggestion",
                "schema_version": 2,
                "request_id": request_id,
                "digest": {
                    "summary": "林舟抵达北港。",
                    "key_events": [{"text": "抵达北港", "fact_ids": [fact_id]}],
                    "character_changes": [],
                    "location_changes": [],
                    "item_changes": [],
                    "relationship_changes": [],
                    "timeline_changes": [],
                },
                "changes": [
                    {
                        "kind": "set_character_field",
                        "subject": "林舟",
                        "field": "location",
                        "value": "北港",
                        "evidence_fact_ids": [fact_id],
                        "certainty": "explicit",
                    }
                ],
                "conflicts": [],
            }
        raise AssertionError("unexpected prompt")


class ChapterMemoryWorkflowTests(TestCase):
    def test_v2_workflow_reuses_fact_and_proposal_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = NovelProject.create(root / "project", "测试")
            project.save_chapter("chapter_01", outline="", content="林舟抵达北港。")
            state = base_state()
            project.save_story_state(state)
            dsh = _MemoryV2DSH()
            workflow = AIWorkflowService(
                dsh,
                fact_cache_root=root / "facts",
                memory_cache_root=root / "memory",
            )

            first = workflow.update_memory(project, "chapter_01")
            first_calls = len(dsh.calls)
            second = workflow.update_memory(project, "chapter_01")

            self.assertEqual(first_calls, 2)
            self.assertFalse(first.cache_hit)
            self.assertTrue(second.cache_hit)
            self.assertEqual(len(dsh.calls), first_calls)
            self.assertEqual(second.summary, "林舟抵达北港。")
            cache_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (root / "memory").rglob("*.json")
            )
            self.assertNotIn("林舟抵达北港。林舟抵达北港。", cache_text)
            cache_values = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in (root / "memory").rglob("*.json")
            ]
            proposal_cache = next(
                item
                for item in cache_values
                if item.get("type") == "chapter_memory_proposal_cache"
            )
            self.assertNotIn("summary", proposal_cache)
            self.assertNotIn("expected_before", proposal_cache["changes"][0])

            refreshed = workflow.update_memory(
                project,
                "chapter_01",
                force_refresh=True,
            )
            self.assertFalse(refreshed.cache_hit)
            self.assertEqual(len(dsh.calls), first_calls + 1)

    def test_oversized_ledger_uses_hierarchical_reduction(self):
        facts = tuple(
            FactRecord(
                f"fact_{index:04d}",
                "character_state",
                f"角色{index}",
                "状态",
                "变化" * 120,
                f"chapter_01:p{index + 1:04d}",
                "explicit",
            )
            for index in range(40)
        )
        ledger = ChapterFactLedger(
            chapter_id="chapter_01",
            chapter_hash="large-ledger",
            chunks=(),
            facts=facts,
            unknowns=(),
            cache_hits=0,
            extracted_chunks=0,
        )

        class ReducingDSH:
            def __init__(self):
                self.calls = []

            def generate_json(self, _system, user, **_kwargs):
                self.calls.append(user)
                ids = list(dict.fromkeys(re.findall(r"fact_[0-9]{4}", user)))
                if "任务类型：chapter_digest_shard" in user:
                    return {
                        "type": "chapter_digest_shard",
                        "schema_version": 1,
                        "summary": "本批次发生若干人物状态变化。",
                        "claims": (
                            [{"text": "人物状态发生变化", "fact_ids": ids[:1]}]
                            if ids
                            else []
                        ),
                    }
                request_id = re.search(r"^请求 ID：(.*)$", user, re.MULTILINE).group(1)
                return {
                    "type": "chapter_memory_suggestion",
                    "schema_version": 2,
                    "request_id": request_id,
                    "digest": {
                        "summary": "多名角色状态发生变化。",
                        "key_events": (
                            [{"text": "人物状态变化", "fact_ids": ids[:1]}]
                            if ids
                            else []
                        ),
                        "character_changes": [],
                        "location_changes": [],
                        "item_changes": [],
                        "relationship_changes": [],
                        "timeline_changes": [],
                    },
                    "changes": [],
                    "conflicts": [],
                }

        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "project", "测试")
            dsh = ReducingDSH()
            proposal = generate_chapter_memory_proposal(
                project,
                ledger,
                dsh,
                base_state={"current_chapter": 1, "characters": {}},
                input_token_budget=4_000,
                reduce_batch_tokens=1_000,
                cache=ChapterMemoryCache(project, Path(tmp) / "cache"),
            )

            self.assertGreater(proposal.reduction_calls, 0)
            self.assertEqual(len(dsh.calls), proposal.reduction_calls + 1)
            self.assertLessEqual(
                DEFAULT_TOKEN_ESTIMATOR.estimate_pair("", dsh.calls[-1]),
                4_000,
            )

            calls_after_first = len(dsh.calls)
            second = generate_chapter_memory_proposal(
                project,
                ledger,
                dsh,
                base_state={
                    "current_chapter": 1,
                    "current_location": "变化后的地点",
                    "characters": {},
                },
                input_token_budget=4_000,
                reduce_batch_tokens=1_000,
                cache=ChapterMemoryCache(project, Path(tmp) / "cache"),
            )
            self.assertEqual(second.reduction_calls, 0)
            self.assertEqual(len(dsh.calls), calls_after_first + 1)
