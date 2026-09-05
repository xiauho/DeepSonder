import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.config import DEFAULT_CONFIG, load_config, normalize_config
from core.context_budget import Section, allocate_with_report, prior_chapter_summaries
from core.history_context import (
    HistoryEntry, HistoryWindow, allocate_history, history_token_budget,
    resolve_history_count, select_history_window,
)
from core.project import NovelProject
from core.prompt_builder import build_expansion_prompt
from core.expansion import run_expansion
from core.token_budget import DEFAULT_TOKEN_ESTIMATOR


class HistoryContextTests(TestCase):
    def test_zero_never_includes_summaries(self):
        source = {"chapter_01": "不应发送"}
        self.assertEqual(select_history_window(source, [], "chapter_02", 0).entries, ())
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "book", "测试")
            project.save_chapter_summaries(source)
            project.save_chapter("chapter_02", title="二", outline="本章规划", content="")
            self.assertEqual(prior_chapter_summaries(project, "chapter_02", count=0), "")
            prompt = build_expansion_prompt(project, "chapter_02", summary_count=0)
        self.assertNotIn("不应发送", prompt.user_prompt)
        self.assertEqual(prompt.report.history_included, 0)
        self.assertEqual(prompt.report.history_missing, 0)

    def test_missing_recent_summary_does_not_backfill_from_outside_range(self):
        result = select_history_window(
            {"chapter_01": "旧", "chapter_02": "前文", "chapter_04": "未来"},
            ["chapter_01", "chapter_02", "chapter_03", "chapter_04"],
            "chapter_04", 2,
        )
        self.assertEqual([item.chapter_id for item in result.entries], ["chapter_02"])
        self.assertEqual(result.missing, 1)
        self.assertEqual(result.in_range, 2)

    def test_atomic_allocation_counts_entries_not_markdown_headings(self):
        entries = tuple(HistoryEntry(f"chapter_{i}", f"第{i}章事实\n### 内部标题\n完整结尾") for i in range(1, 4))
        window = HistoryWindow(entries, 3, 3, 0)
        tokens = DEFAULT_TOKEN_ESTIMATOR.estimate(entries[-1].rendered)
        result = allocate_history(window, 10_000, tokens)
        self.assertEqual(result.text, entries[-1].rendered)
        self.assertEqual(result.included, 1)
        self.assertEqual(result.excluded_budget, 2)
        self.assertLessEqual(result.estimated_tokens, tokens)

    def test_non_history_sections_are_reserved_and_entries_stay_whole(self):
        entry = HistoryEntry("chapter_01", "原文完整结尾")
        history = HistoryWindow((entry,), 1, 1, 0)
        sections = [
            Section("summaries", entry.rendered, 6000, "tail", 3, history),
            Section("characters", "人" * 200, 200, "head", 4),
        ]
        result = allocate_with_report(sections, 210, history_token_limit=1000)
        self.assertEqual(result.values["characters"], "人" * 200)
        self.assertEqual(result.history.included, 0)
        self.assertEqual(result.history.excluded_budget, 1)

    def test_config_migration_preserves_explicit_zero_even_with_merged_defaults(self):
        for count in (0, 5, 20):
            with self.subTest(count=count), TemporaryDirectory() as tmp:
                target = Path(tmp) / "config.json"
                target.write_text(json.dumps({
                    "config_schema_version": 3, "ai_context_history_chapters": count,
                }), encoding="utf-8")
                with patch("core.config.get_config_path", return_value=target):
                    config = load_config()
                self.assertEqual(config["ai_history_mode"], "custom")
                self.assertEqual(config["ai_context_history_chapters"], count)
                self.assertEqual(config, normalize_config(config))
                self.assertEqual(json.loads(target.read_text(encoding="utf-8")), config)
        self.assertEqual(normalize_config(DEFAULT_CONFIG)["ai_history_mode"], "auto")

    def test_policy_follows_strategy_and_preserves_custom_count(self):
        for strategy, count, percent in (("compatible", 3, 15), ("balanced", 5, 20), ("deep", 8, 25)):
            self.assertEqual(resolve_history_count("auto", 100, strategy), count)
            self.assertEqual(resolve_history_count("custom", 0, strategy), 0)
            self.assertEqual(history_token_budget(128000, strategy), 128000 * percent // 100)

    def test_expansion_and_retry_use_same_resolved_policy_and_report_missing(self):
        from tests.test_expansion import FakeDSH
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "book", "测试")
            for i in range(1, 11):
                project.save_chapter(f"chapter_{i:02}", title=str(i), outline="前行", content="")
            project.save_chapter_summaries({f"chapter_{i:02}": f"第{i}章事实" for i in range(1, 9)})
            dsh = FakeDSH(["我已就绪。请问这次需要我做什么？", f"<NOVEL_TEXT>{'字' * 300}</NOVEL_TEXT>"])
            dsh.context_strategy = "deep"
            dsh.input_token_budget = 128_000
            run_expansion(project, "chapter_10", dsh, target_chars=300, history_mode="auto")
        self.assertEqual(len(dsh.calls), 2)
        for call in dsh.calls:
            report = call["context_report"]
            self.assertEqual(report.history_requested, 8)
            self.assertEqual(report.history_missing, 1)
            self.assertEqual(report.history_included, 7)
            self.assertEqual(report.history_token_budget, 32_000)
            self.assertNotIn("第1章事实", call["user"])
            self.assertNotIn("第2章事实", json.dumps(report.to_dict(), ensure_ascii=False))
