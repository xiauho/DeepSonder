from unittest import TestCase

from core.config import (
    AI_CONTEXT_HISTORY_CHAPTERS_MAX,
    DEFAULT_CONFIG,
    DSH_FILE_PROMPT_BUDGET_DEFAULT,
    DSH_FILE_PROMPT_BUDGET_MAX,
    DSH_TASK_FILE_MAX_BYTES_DEFAULT,
    DSH_TASK_FILE_MAX_BYTES_MAX,
    _normalize_config,
)
from ui.theme import DARK_COLORS, LIGHT_COLORS, build_qss, document_css
from core.token_budget import (
    DEFAULT_CHUNK_OVERLAP_TOKENS,
    DEFAULT_CHUNK_TOKEN_BUDGET,
    DEFAULT_INPUT_TOKEN_BUDGET,
    DEFAULT_RUNTIME_RESERVE_TOKENS,
)


class ThemeConfigTests(TestCase):
    def test_light_theme_defaults_stay_in_sync(self) -> None:
        for key, value in LIGHT_COLORS.items():
            self.assertEqual(DEFAULT_CONFIG[key], value)

    def test_dark_theme_qss_uses_dark_accent(self) -> None:
        qss = build_qss({"theme": "dark"})
        self.assertIn("QSplitter#mainSplitter::handle", qss)
        self.assertIn(DARK_COLORS["accent_color"], qss)

    def test_foreshadowing_selection_list_uses_themed_indicator(self) -> None:
        light_qss = build_qss({"theme": "light"})
        dark_qss = build_qss({"theme": "dark"})

        selector = "QListWidget#foreshadowingSelectionList::indicator"
        self.assertIn(selector, light_qss)
        self.assertIn(selector, dark_qss)
        self.assertIn("QListWidget#foreshadowingSelectionList {", light_qss)
        self.assertIn("selection-background-color: transparent", light_qss)
        self.assertIn("QListWidget#foreshadowingSelectionList::item:hover", light_qss)
        self.assertIn("QListWidget#powerSelectionList {", light_qss)
        self.assertIn("QListWidget#powerSelectionList::indicator", light_qss)
        # The light theme uses the panel color for an unchecked indicator;
        # the dark theme uses the field color to preserve contrast.
        self.assertIn(LIGHT_COLORS["panel_color"], light_qss)
        self.assertIn(DARK_COLORS["field_color"], dark_qss)

    def test_message_box_qss_uses_theme_colors(self) -> None:
        qss = build_qss({"theme": "light"})
        self.assertIn("QMessageBox", qss)
        self.assertIn(LIGHT_COLORS["panel_color"], qss)
        self.assertIn(LIGHT_COLORS["text_color"], qss)

    def test_trash_dialog_has_opaque_theme_surfaces(self) -> None:
        qss = build_qss({"theme": "light"})
        self.assertIn("QDialog#trashDialog", qss)
        self.assertIn("QListWidget#trashList", qss)
        self.assertLess(qss.index("QWidget { background: transparent; }"), qss.index("QDialog { background:"))
        self.assertIn(LIGHT_COLORS["field_color"], qss)

    def test_document_css_switches_browser_field_background(self) -> None:
        light_css = document_css({"theme": "light"})
        dark_css = document_css({"theme": "dark"})
        self.assertIn(LIGHT_COLORS["field_color"], light_css)
        self.assertIn(DARK_COLORS["field_color"], dark_css)
        self.assertNotEqual(light_css, dark_css)

    def test_ui_language_defaults_to_chinese(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["ui_language"], "zh-CN")

    def test_expansion_length_migrates_from_legacy_setting(self) -> None:
        config = {"continue_target_chars": 2600}
        _normalize_config(config)
        self.assertEqual(config["chapter_target_chars"], 2600)
        self.assertNotIn("continue_target_chars", config)
        self.assertNotIn("expand_target_chars", config)

    def test_target_chapter_length_defaults_to_three_thousand(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["chapter_target_chars"], 3000)

    def test_history_chapter_setting_is_bounded(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["ai_context_history_chapters"], 5)
        config = {"ai_context_history_chapters": 999}
        _normalize_config(config)
        self.assertEqual(AI_CONTEXT_HISTORY_CHAPTERS_MAX, 200)
        self.assertEqual(config["ai_context_history_chapters"], 200)

        config = {"ai_context_history_chapters": -3}
        _normalize_config(config)
        self.assertEqual(config["ai_context_history_chapters"], 0)

    def test_file_business_transport_limits_are_normalized(self) -> None:
        self.assertNotIn("dsh_prompt_transport", DEFAULT_CONFIG)
        self.assertEqual(
            DEFAULT_CONFIG["dsh_file_prompt_budget"],
            DSH_FILE_PROMPT_BUDGET_DEFAULT,
        )
        self.assertEqual(
            DEFAULT_CONFIG["dsh_task_file_max_bytes"],
            DSH_TASK_FILE_MAX_BYTES_DEFAULT,
        )

        config = {
            "dsh_prompt_transport": "invalid",
            "dsh_file_prompt_budget": 999_999,
            "dsh_task_file_max_bytes": 9_999_999,
        }
        _normalize_config(config)
        self.assertNotIn("dsh_prompt_transport", config)
        self.assertEqual(config["dsh_file_prompt_budget"], 20_424)
        self.assertEqual(config["dsh_task_file_max_bytes"], 512_000)

        migrated = {"dsh_prompt_transport": "auto"}
        _normalize_config(migrated)
        self.assertNotIn("dsh_prompt_transport", migrated)

    def test_token_budget_settings_are_normalized(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["ai_input_token_budget"], DEFAULT_INPUT_TOKEN_BUDGET)
        self.assertEqual(
            DEFAULT_CONFIG["ai_runtime_reserve_tokens"],
            DEFAULT_RUNTIME_RESERVE_TOKENS,
        )
        self.assertEqual(DEFAULT_CONFIG["ai_chunk_token_budget"], DEFAULT_CHUNK_TOKEN_BUDGET)
        self.assertEqual(
            DEFAULT_CONFIG["ai_chunk_overlap_tokens"],
            DEFAULT_CHUNK_OVERLAP_TOKENS,
        )
        config = {
            "ai_input_token_budget": 999_999,
            "ai_runtime_reserve_tokens": -1,
            "ai_chunk_token_budget": 900,
            "ai_chunk_overlap_tokens": 999,
            "ai_memory_pipeline": "unsafe",
        }
        _normalize_config(config)
        self.assertEqual(config["ai_input_token_budget"], 24_000)
        self.assertEqual(config["ai_runtime_reserve_tokens"], 6_000)
        self.assertEqual(config["ai_chunk_token_budget"], 1_000)
        self.assertEqual(config["ai_chunk_overlap_tokens"], 333)
        self.assertNotIn("ai_memory_pipeline", config)

        configured = {
            "ai_model_context_window_tokens": 1_000_000,
            "ai_context_strategy": "balanced",
            "ai_input_token_budget": 4_000,
            "dsh_file_prompt_budget": 20_000,
        }
        _normalize_config(configured)
        self.assertEqual(configured["ai_input_token_budget"], 128_000)
        self.assertEqual(configured["dsh_file_prompt_budget"], 110_859)
        self.assertEqual(configured["ai_runtime_reserve_tokens"], 64_000)
