import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.accepted_memory import AcceptedMemoryView, accepted_memory_path, load_accepted_memory
from core.ai_result_service import AIResultService
from core.chapter_facts import FactRecord
from core.chapter_memory import ChapterDigest, ChapterMemoryProposal, DigestEntry, canonical_hash
from core.context_budget import build_ai_context
from core.context_profiles import EXPANSION_CONTEXT_PROFILE
from core.history_context import allocate_history, select_history_window
from core.project import NovelProject
from core.project_data import ProjectDataStore
from core.prompt_builder import build_expansion_prompt
from core.text_chunking import chapter_content_hash


class AcceptedMemoryTests(TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = NovelProject.create(Path(self.tmp.name) / "book", "测试")
        self.project.save_chapter("chapter_500", outline="林舟寻找北港的钥匙", content="当前正文")
        self.project.write_file(self.project.canon_dir / "characters" / "林舟.md", "# 林舟\n别名：小舟\n")

    def proposal(self, key="chapter_30", text="林舟将北港钥匙藏入旧井。"):
        self.project.save_chapter(key, outline="", content=text)
        base = self.project.load_story_state()
        state = {**base, "current_location": text}
        fact = FactRecord("fact_1", "item", "林舟", "藏入", text, f"{key}:p0001", "explicit")
        return ChapterMemoryProposal(
            key, chapter_content_hash(text), canonical_hash(base), "context",
            ChapterDigest(text, key_events=(DigestEntry(text, ("fact_1",)),)),
            (), (), state, evidence_facts=(fact,),
        )

    def adopt(self, key="chapter_30", text="林舟将北港钥匙藏入旧井。"):
        proposal = self.proposal(key, text)
        AIResultService.commit_memory_proposal(self.project, key, proposal)
        return proposal

    def window(self, query="林舟寻找北港钥匙", count=1):
        summaries = self.project.load_chapter_summaries()
        view = AcceptedMemoryView(self.project, summaries)
        return select_history_window(
            summaries, list(view.paths), "chapter_500", count,
            view=view, query=query, remote_enabled=True,
        )

    def test_only_adoption_creates_portable_provenance_and_evidence(self):
        proposal = self.proposal()
        self.assertFalse(accepted_memory_path(self.project).exists())
        self.assertEqual(self.window().remote, ())
        AIResultService.commit_memory_proposal(self.project, "chapter_30", proposal)
        record = load_accepted_memory(self.project)["chapter_30"]
        self.assertEqual(record["source_hash"], proposal.chapter_hash)
        self.assertEqual(record["evidence_facts"][0]["anchor"], "chapter_30:p0001")
        self.assertEqual(AcceptedMemoryView(self.project, self.project.load_chapter_summaries()).status("chapter_30"), "verified")

    def test_chapter_500_retrieves_chapter_30_and_excludes_future(self):
        self.adopt()
        self.adopt("chapter_499", "最近发生无关宴会。")
        self.adopt("chapter_600", "林舟未来秘密标记：北港钥匙被毁。")
        window = self.window()
        self.assertEqual([e.chapter_id for e in window.remote], ["chapter_30"])
        allocated = allocate_history(window, 10000, 5000)
        self.assertEqual(allocated.included, 1)
        self.assertEqual(allocated.remote_included, 1)
        self.assertNotIn("未来秘密标记", allocated.text)
        self.assertIn("fact_1", allocated.text)
        self.assertEqual(allocated, allocate_history(self.window(), 10000, 5000))
        report = build_expansion_prompt(self.project, "chapter_500", summary_count=1).report
        self.assertEqual(report.history_remote_included, 1)
        self.assertNotIn("藏入旧井", json.dumps(report.to_dict(), ensure_ascii=False))

    def test_alias_matching_and_no_irrelevant_filler(self):
        self.adopt()
        self.adopt("chapter_499", "晚宴结束。")
        self.assertEqual(len(self.window(query="小舟返回").remote), 1)
        self.assertEqual(self.window(query="沙漠商队渡过赤河").remote, ())

    def test_modified_source_and_summary_are_excluded(self):
        self.adopt()
        self.adopt("chapter_499", "晚宴结束。")
        self.project.save_chapter("chapter_30", outline="", content="林舟不曾取得钥匙。")
        window = self.window()
        self.assertEqual(window.remote, ())
        self.assertEqual(window.stale, 1)
        summaries = self.project.load_chapter_summaries()
        summaries["chapter_499"] = "手动修改摘要"
        self.project.save_chapter_summaries(summaries)
        self.assertEqual(self.window().entries, ())

    def test_deleted_chapter_not_retrieved_and_restore_can_revalidate(self):
        self.adopt()
        self.adopt("chapter_499", "晚宴结束。")
        store = ProjectDataStore(self.project)
        trash = store.move_chapter_to_trash("chapter_30")
        self.assertEqual(self.window().remote, ())
        store.restore_trash_item(trash.trash_id)
        self.assertEqual(len(self.window().remote), 1)

    def test_historical_task_uses_prior_snapshot_not_future_state(self):
        self.adopt()
        self.adopt("chapter_600", "未来秘密标记")
        context = build_ai_context(self.project, "chapter_500", profile=EXPANSION_CONTEXT_PROFILE)
        self.assertEqual(context.state_scope, "snapshot:chapter_30")
        self.assertNotIn("未来秘密标记", str(context.story_state))
        self.project.save_chapter("chapter_30", outline="", content="旧章已改写")
        context = build_ai_context(self.project, "chapter_500", profile=EXPANSION_CONTEXT_PROFILE)
        self.assertEqual(context.story_state, {})

    def test_snapshot_invalidates_when_earlier_dependency_changes(self):
        self.project.save_chapter("chapter_02", outline="", content="旧线索")
        self.adopt()
        self.project.save_chapter("chapter_02", outline="", content="新线索")
        context = build_ai_context(self.project, "chapter_500", profile=EXPANSION_CONTEXT_PROFILE)
        self.assertEqual(context.story_state, {})

    def test_corrupt_records_do_not_fall_back_to_trusted_history(self):
        self.adopt()
        accepted_memory_path(self.project).write_text("{broken", encoding="utf-8")
        window = self.window()
        self.assertTrue(window.provenance_error)
        self.assertFalse(window.entries)
        self.assertFalse(window.remote)

    def test_failed_provenance_write_rolls_back_summary_and_state(self):
        proposal = self.proposal()
        summaries, state = self.project.load_chapter_summaries(), self.project.load_story_state()
        with patch("core.accepted_memory.save_accepted_memory", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                AIResultService.commit_memory_proposal(self.project, "chapter_30", proposal)
        self.assertEqual(self.project.load_chapter_summaries(), summaries)
        self.assertEqual(self.project.load_story_state(), state)
        self.assertFalse(accepted_memory_path(self.project).exists())

    def test_zero_and_remote_toggle_disable_retrieval(self):
        self.adopt()
        self.adopt("chapter_499", "晚宴结束。")
        self.assertFalse(self.window(count=0).remote)
        from dataclasses import replace
        context = build_ai_context(self.project, "chapter_500", profile=EXPANSION_CONTEXT_PROFILE)
        context = replace(context, history_remote_enabled=False)
        prompt = build_expansion_prompt(self.project, "chapter_500", summary_count=1, context=context)
        self.assertEqual(prompt.report.history_remote_included, 0)

    def test_legacy_summary_is_recent_only_and_marked_unverified(self):
        self.project.save_chapter_summaries({"chapter_30": "林舟藏起北港钥匙", "chapter_499": "旧摘要"})
        window = self.window()
        self.assertEqual(window.unverified, 1)
        self.assertFalse(window.remote)
        self.assertIn("正文版本未确认", allocate_history(window, 10000, 5000).text)

    def test_recent_has_priority_and_remote_shares_hard_budget(self):
        self.adopt()
        self.adopt("chapter_499", "最近的晚宴。")
        window = self.window()
        recent_chars = len(window.entries[0].rendered)
        allocated = allocate_history(window, recent_chars, 5000)
        self.assertEqual(allocated.included, 1)
        self.assertEqual(allocated.remote_included, 0)
        self.assertLessEqual(len(allocated.text), recent_chars)
        self.assertFalse(allocate_history(window, 10000, 1).text)

    def test_tampered_record_is_not_retrieved(self):
        self.adopt()
        self.adopt("chapter_499", "晚宴结束。")
        from core.accepted_memory import save_accepted_memory
        records = load_accepted_memory(self.project)
        records["chapter_30"]["digest"]["key_events"][0]["text"] = "林舟改为销毁北港钥匙"
        save_accepted_memory(self.project, records)
        self.assertFalse(self.window().remote)
        self.assertEqual(self.window().stale, 1)
