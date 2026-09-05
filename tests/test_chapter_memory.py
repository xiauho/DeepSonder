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
    canonical_hash,
    generate_chapter_memory_proposal,
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
        "type": "chapter_memory_proposal",
        "schema_version": 1,
        "chapter_id": ledger.chapter_id,
        "chapter_hash": ledger.chapter_hash,
        "base_state_hash": canonical_hash(state),
        "context_hash": context_hash,
        "summary": "林舟离开旧站并抵达北港。",
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
        "patches": [
            {
                "kind": "set_character_field",
                "subject": "林舟",
                "field": "location",
                "expected_before": "旧站",
                "value": "北港",
                "evidence_fact_ids": ["fact_location"],
                "certainty": "explicit",
            }
        ],
        "conflicts": [],
        "completion_message": "章节记忆提案已生成",
    }


class ChapterMemoryProtocolTests(TestCase):
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
        value["patches"][0]["evidence_fact_ids"] = ["fact_missing"]

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
        value["patches"][0]["kind"] = "set_character_relation"
        value["patches"][0]["field"] = "顾青"
        value["patches"][0]["expected_before"] = None
        value["patches"][0]["value"] = "盟友"

        proposal = parse_memory_proposal(
            value,
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertTrue(proposal.has_blockers)
        self.assertIn("unsupported_patch", {item.kind for item in proposal.conflicts})

    def test_wrong_precondition_creates_blocker_and_does_not_apply_patch(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        value["patches"][0]["expected_before"] = "错误地点"

        proposal = parse_memory_proposal(
            value,
            ledger,
            state,
            expected_context_hash="context-hash",
        )

        self.assertTrue(proposal.has_blockers)
        self.assertEqual(
            proposal.resulting_state["characters"]["林舟"]["location"],
            "旧站",
        )
        self.assertIn("state_precondition", {item.kind for item in proposal.conflicts})

    def test_colliding_patch_targets_create_blocker(self):
        ledger = make_ledger()
        state = base_state()
        value = valid_proposal(ledger, state)
        second = dict(value["patches"][0])
        second["value"] = "南城"
        value["patches"].append(second)

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
        value["patches"][0]["certainty"] = "inferred"

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
            value["patches"][0]["expected_before"] = "错误地点"
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
        if "任务类型：chapter_memory_proposal" in user_prompt:
            fact_id = re.search(r'"fact_id":"(fact_[a-f0-9]+)"', user_prompt).group(1)
            chapter_id = re.search(r"^章节 ID：(.*)$", user_prompt, re.MULTILINE).group(1)
            chapter_hash = re.search(r"^章节正文哈希：(.*)$", user_prompt, re.MULTILINE).group(1)
            state_hash = re.search(r"^旧状态哈希：(.*)$", user_prompt, re.MULTILINE).group(1)
            context_hash = re.search(r"^相关设定哈希：(.*)$", user_prompt, re.MULTILINE).group(1)
            state_json = re.search(
                r"【相关旧故事状态】\n(.*?)\n\n【相关角色卡",
                user_prompt,
                re.DOTALL,
            ).group(1)
            old_state = json.loads(state_json)
            old_location = old_state.get("characters", {}).get("林舟", {}).get("location")
            return {
                "type": "chapter_memory_proposal",
                "schema_version": 1,
                "chapter_id": chapter_id,
                "chapter_hash": chapter_hash,
                "base_state_hash": state_hash,
                "context_hash": context_hash,
                "summary": "林舟抵达北港。",
                "digest": {
                    "summary": "林舟抵达北港。",
                    "key_events": [{"text": "抵达北港", "fact_ids": [fact_id]}],
                    "character_changes": [],
                    "location_changes": [],
                    "item_changes": [],
                    "relationship_changes": [],
                    "timeline_changes": [],
                },
                "patches": [
                    {
                        "kind": "set_character_field",
                        "subject": "林舟",
                        "field": "location",
                        "expected_before": old_location,
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
                chapter_id = re.search(r"^章节 ID：(.*)$", user, re.MULTILINE).group(1)
                chapter_hash = re.search(r"^章节正文哈希：(.*)$", user, re.MULTILINE).group(1)
                state_hash = re.search(r"^旧状态哈希：(.*)$", user, re.MULTILINE).group(1)
                context_hash = re.search(r"^相关设定哈希：(.*)$", user, re.MULTILINE).group(1)
                return {
                    "type": "chapter_memory_proposal",
                    "schema_version": 1,
                    "chapter_id": chapter_id,
                    "chapter_hash": chapter_hash,
                    "base_state_hash": state_hash,
                    "context_hash": context_hash,
                    "summary": "多名角色状态发生变化。",
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
                    "patches": [],
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
