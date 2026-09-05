from unittest import TestCase

from core.token_budget import ConservativeTokenEstimator, TokenBudget


class ConservativeTokenEstimatorTests(TestCase):
    def setUp(self) -> None:
        self.estimator = ConservativeTokenEstimator()

    def test_empty_text_is_zero_and_estimates_are_deterministic(self) -> None:
        self.assertEqual(self.estimator.estimate(""), 0)
        text = "林舟抵达旧站。\njson={\"ok\":true}"
        self.assertEqual(self.estimator.estimate(text), self.estimator.estimate(text))

    def test_chinese_and_json_punctuation_are_conservatively_counted(self) -> None:
        chinese = self.estimator.estimate("天地玄黄" * 20)
        english = self.estimator.estimate("abcd" * 20)
        punctuation = self.estimator.estimate("{}[],:" * 20)
        self.assertGreater(chinese, english)
        self.assertGreater(punctuation, 20)

    def test_pair_includes_message_framing_allowance(self) -> None:
        pair = self.estimator.estimate_pair("system", "user")
        separate = self.estimator.estimate("system") + self.estimator.estimate("user")
        self.assertGreater(pair, separate)


class TokenBudgetTests(TestCase):
    def test_accepts_only_inputs_within_limit(self) -> None:
        budget = TokenBudget(input_limit=100, runtime_reserve=20, output_reserve=10)
        self.assertTrue(budget.accepts(100))
        self.assertFalse(budget.accepts(101))
        self.assertEqual(budget.reserved_tokens, 30)

    def test_declared_model_window_includes_runtime_and_output_reserves(self) -> None:
        budget = TokenBudget(
            input_limit=100,
            runtime_reserve=20,
            output_reserve=10,
            model_context_window=120,
        )
        self.assertTrue(budget.accepts(90))
        self.assertFalse(budget.accepts(91))
