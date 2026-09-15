from unittest import TestCase

from core.context_capacity import derive_context_capacity


class ContextCapacityTests(TestCase):
    def test_unknown_model_keeps_safe_legacy_budget(self) -> None:
        capacity = derive_context_capacity(0, "deep")

        self.assertFalse(capacity.verified)
        self.assertEqual(capacity.input_token_budget, 24_000)
        self.assertEqual(capacity.runtime_reserve_tokens, 6_000)
        self.assertEqual(capacity.prompt_char_budget, 20_424)

    def test_common_presets_match_recommended_input_budgets(self) -> None:
        self.assertEqual(
            derive_context_capacity(256_000, "balanced").input_token_budget,
            64_000,
        )
        self.assertEqual(
            derive_context_capacity(1_000_000, "balanced").input_token_budget,
            128_000,
        )
        deep = derive_context_capacity(1_000_000, "deep")
        self.assertEqual(deep.input_token_budget, 256_000)
        self.assertGreater(deep.task_file_max_bytes, 512_000)

    def test_custom_window_interpolates_and_respects_combined_ceiling(self) -> None:
        capacity = derive_context_capacity(192_000, "deep")

        self.assertGreater(capacity.input_token_budget, 80_000)
        self.assertLess(capacity.input_token_budget, 128_000)
        self.assertLessEqual(
            capacity.input_token_budget + capacity.runtime_reserve_tokens,
            capacity.model_context_window_tokens,
        )
