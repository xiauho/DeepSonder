"""Conservative token estimates for bounded AI task construction.

DeepSeek Harness owns the selected model and does not currently expose that
model's tokenizer through the headless contract.  Novalist therefore uses a
deliberately conservative, deterministic estimator.  It is an admission and
diagnostic aid, not a claim about provider billing tokens.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


DEFAULT_INPUT_TOKEN_BUDGET = 24_000
DEFAULT_RUNTIME_RESERVE_TOKENS = 6_000
DEFAULT_CHUNK_TOKEN_BUDGET = 5_000
DEFAULT_CHUNK_OVERLAP_TOKENS = 300
DEFAULT_TOKEN_SAFETY_FACTOR = 1.15
TOKEN_ESTIMATOR_NAME = "conservative_v1"
MESSAGE_FRAMING_TOKEN_ALLOWANCE = 128

_ASCII_WORD = re.compile(r"[A-Za-z0-9_]+")


@dataclass(frozen=True)
class TokenBudget:
    """One explicit Novalist-side input ceiling and reserved model capacity."""

    input_limit: int = DEFAULT_INPUT_TOKEN_BUDGET
    runtime_reserve: int = DEFAULT_RUNTIME_RESERVE_TOKENS
    output_reserve: int = 0
    safety_margin: int = 0
    model_context_window: int = 0

    def accepts(self, estimated_input: int) -> bool:
        estimated = max(0, int(estimated_input))
        if estimated > max(1, int(self.input_limit)):
            return False
        if self.model_context_window > 0:
            return estimated + self.reserved_tokens <= self.model_context_window
        return True

    @property
    def reserved_tokens(self) -> int:
        return sum(
            max(0, int(value))
            for value in (
                self.runtime_reserve,
                self.output_reserve,
                self.safety_margin,
            )
        )


class ConservativeTokenEstimator:
    """Estimate mixed Chinese/English prompt size without model dependencies."""

    name = TOKEN_ESTIMATOR_NAME

    def __init__(self, safety_factor: float = DEFAULT_TOKEN_SAFETY_FACTOR):
        try:
            factor = float(safety_factor)
        except (TypeError, ValueError):
            factor = DEFAULT_TOKEN_SAFETY_FACTOR
        self.safety_factor = max(1.0, min(2.0, factor))

    def estimate(self, text: object) -> int:
        value = str(text or "")
        if not value:
            return 0

        ascii_word_tokens = sum(
            max(1, math.ceil(len(match.group(0)) / 4))
            for match in _ASCII_WORD.finditer(value)
        )
        base = ascii_word_tokens
        in_ascii_word = [False] * len(value)
        for match in _ASCII_WORD.finditer(value):
            in_ascii_word[match.start() : match.end()] = [True] * (
                match.end() - match.start()
            )

        for index, character in enumerate(value):
            if in_ascii_word[index]:
                continue
            if character in " \t":
                continue
            if character in "\r\n":
                base += 1
                continue
            # CJK, emoji and non-ASCII symbols conservatively count as one;
            # ASCII JSON/Markdown punctuation also counts as one.
            base += 1
        return max(1, math.ceil(base * self.safety_factor))

    def estimate_pair(self, system_prompt: object, user_prompt: object) -> int:
        # Leave a small stable allowance for role/message framing performed by
        # Harness in addition to the visible prompt text.
        return (
            self.estimate(system_prompt)
            + self.estimate(user_prompt)
            + MESSAGE_FRAMING_TOKEN_ALLOWANCE
        )


DEFAULT_TOKEN_ESTIMATOR = ConservativeTokenEstimator()
