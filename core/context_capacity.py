"""Derive safe DeepSonder prompt limits from a declared model context window."""

from __future__ import annotations

from dataclasses import dataclass

from .token_budget import DEFAULT_TOKEN_SAFETY_FACTOR


MODEL_CONTEXT_WINDOW_PRESETS = (128_000, 256_000, 512_000, 1_000_000)
MODEL_CONTEXT_WINDOW_MIN = 32_000
MODEL_CONTEXT_WINDOW_MAX = 2_000_000
DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS = 0  # Unknown until the user or DSH confirms it.

CONTEXT_STRATEGY_COMPATIBLE = "compatible"
CONTEXT_STRATEGY_BALANCED = "balanced"
CONTEXT_STRATEGY_DEEP = "deep"
CONTEXT_STRATEGIES = (
    CONTEXT_STRATEGY_COMPATIBLE,
    CONTEXT_STRATEGY_BALANCED,
    CONTEXT_STRATEGY_DEEP,
)
DEFAULT_CONTEXT_STRATEGY = CONTEXT_STRATEGY_BALANCED

UNKNOWN_INPUT_TOKEN_BUDGET = 24_000
UNKNOWN_RUNTIME_RESERVE_TOKENS = 6_000
PROMPT_FRAMING_TOKEN_ALLOWANCE = 512
MIN_FILE_PROMPT_CHARS = 20_000
MIN_TASK_FILE_BYTES = 512_000
MAX_TASK_FILE_BYTES = 4_000_000

_INPUT_BUDGETS = {
    128_000: {
        CONTEXT_STRATEGY_COMPATIBLE: 24_000,
        CONTEXT_STRATEGY_BALANCED: 48_000,
        CONTEXT_STRATEGY_DEEP: 80_000,
    },
    256_000: {
        CONTEXT_STRATEGY_COMPATIBLE: 32_000,
        CONTEXT_STRATEGY_BALANCED: 64_000,
        CONTEXT_STRATEGY_DEEP: 128_000,
    },
    512_000: {
        CONTEXT_STRATEGY_COMPATIBLE: 48_000,
        CONTEXT_STRATEGY_BALANCED: 96_000,
        CONTEXT_STRATEGY_DEEP: 192_000,
    },
    1_000_000: {
        CONTEXT_STRATEGY_COMPATIBLE: 64_000,
        CONTEXT_STRATEGY_BALANCED: 128_000,
        CONTEXT_STRATEGY_DEEP: 256_000,
    },
}

_RUNTIME_RESERVES = {
    128_000: 16_000,
    256_000: 32_000,
    512_000: 48_000,
    1_000_000: 64_000,
}


@dataclass(frozen=True)
class ContextCapacity:
    """One normalized model declaration and all limits derived from it."""

    model_context_window_tokens: int
    strategy: str
    input_token_budget: int
    runtime_reserve_tokens: int
    prompt_char_budget: int
    task_file_max_bytes: int
    verified: bool


def normalize_model_context_window(value: object) -> int:
    """Normalize 0 as unknown and bound explicit custom declarations."""
    try:
        window = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS
    if window <= 0:
        return DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS
    return max(MODEL_CONTEXT_WINDOW_MIN, min(MODEL_CONTEXT_WINDOW_MAX, window))


def normalize_context_strategy(value: object) -> str:
    strategy = str(value or "").strip().lower()
    return strategy if strategy in CONTEXT_STRATEGIES else DEFAULT_CONTEXT_STRATEGY


def derive_context_capacity(
    model_context_window_tokens: object,
    strategy: object = DEFAULT_CONTEXT_STRATEGY,
) -> ContextCapacity:
    """Return deterministic limits without treating model capacity as prompt size."""
    window = normalize_model_context_window(model_context_window_tokens)
    normalized_strategy = normalize_context_strategy(strategy)
    if window <= 0:
        input_budget = UNKNOWN_INPUT_TOKEN_BUDGET
        runtime_reserve = UNKNOWN_RUNTIME_RESERVE_TOKENS
        verified = False
    else:
        input_budget = _interpolate(window, normalized_strategy, _INPUT_BUDGETS)
        runtime_reserve = _interpolate(window, None, _RUNTIME_RESERVES)
        # The declared context is a combined input/output ceiling. A custom
        # value below a preset must never produce a derived total above it.
        hard_input_ceiling = max(4_000, window - runtime_reserve)
        input_budget = min(input_budget, hard_input_ceiling)
        verified = True

    safe_chars = max(
        1_000,
        int(
            max(1_000, input_budget - PROMPT_FRAMING_TOKEN_ALLOWANCE)
            / DEFAULT_TOKEN_SAFETY_FACTOR
        ),
    )
    prompt_chars = max(MIN_FILE_PROMPT_CHARS, safe_chars)
    # Four bytes per character covers UTF-8 CJK/emoji without coupling the
    # transport file limit to one language's average encoding width.
    task_bytes = max(MIN_TASK_FILE_BYTES, prompt_chars * 4 + 32_768)
    task_bytes = min(MAX_TASK_FILE_BYTES, task_bytes)
    return ContextCapacity(
        model_context_window_tokens=window,
        strategy=normalized_strategy,
        input_token_budget=input_budget,
        runtime_reserve_tokens=runtime_reserve,
        prompt_char_budget=prompt_chars,
        task_file_max_bytes=task_bytes,
        verified=verified,
    )


def _interpolate(
    window: int,
    strategy: str | None,
    table: dict[int, object],
) -> int:
    points = sorted(table)

    def value(point: int) -> int:
        entry = table[point]
        if isinstance(entry, dict):
            return int(entry[strategy])
        return int(entry)

    if window <= points[0]:
        return value(points[0])
    if window >= points[-1]:
        return value(points[-1])
    for lower, upper in zip(points, points[1:]):
        if lower <= window <= upper:
            ratio = (window - lower) / (upper - lower)
            return round(value(lower) + (value(upper) - value(lower)) * ratio)
    return value(points[-1])
