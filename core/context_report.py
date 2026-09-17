"""Redacted diagnostics for one AI prompt and its DSH transport."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable

from .context_selection import CanonSelectionStat


@dataclass(frozen=True)
class SectionUsage:
    """Character counts for one prompt section, never the section text."""

    key: str
    source_chars: int
    sent_chars: int
    status: str
    priority: int
    keep: str


@dataclass(frozen=True)
class PromptContextReport:
    """Prompt construction and invocation metrics with all prose removed."""

    schema_version: int
    task_kind: str
    chapter_id: str
    prompt_budget: int
    context_budget: int
    overhead_chars: int
    system_prompt_chars: int
    user_prompt_chars: int
    total_prompt_chars: int
    sections: tuple[SectionUsage, ...] = ()
    selections: tuple[CanonSelectionStat, ...] = ()
    history_requested: int | None = None
    history_available: int | None = None
    history_included: int | None = None
    history_in_range: int = 0
    history_missing: int = 0
    history_excluded_budget: int = 0
    history_token_budget: int = 0
    history_estimated_tokens: int = 0
    history_remote_candidates: int = 0
    history_remote_matched: int = 0
    history_remote_included: int = 0
    history_stale: int = 0
    history_unverified: int = 0
    history_provenance_error: bool = False
    history_sources: tuple[dict, ...] = ()
    style_samples: tuple[dict, ...] = ()
    state_scope: str = ""
    transport: str = "pending"
    submitted_prompt_chars: int = 0
    command_chars: int = 0
    outcome: str = "pending"
    task_file_cleaned: bool = True
    task_file_bytes: int = 0
    file_ack_verified: bool = False
    file_ack_retry_count: int = 0
    file_ack_error: str = ""
    input_token_budget: int = 0
    runtime_reserve_tokens: int = 0
    estimated_input_tokens: int = 0
    token_estimator: str = ""
    model_context_window_tokens: int = 0
    context_strategy: str = ""

    @property
    def health(self) -> str:
        """Return a stable UI severity derived only from allocation states."""
        if (
            self.input_token_budget > 0
            and self.estimated_input_tokens > self.input_token_budget
        ):
            return "critical"
        if any(
            item.required > 0
            and item.prompt_included is not None
            and item.prompt_included < item.required
            for item in self.selections
        ):
            return "critical"
        if any(item.status == "dropped" and item.priority <= 2 for item in self.sections):
            return "critical"
        if any(item.status in {"capped", "trimmed", "dropped"} for item in self.sections):
            return "partial"
        if self.history_missing or self.history_stale or self.history_unverified or self.history_provenance_error:
            return "partial"
        if self.state_scope in {"legacy_unverified", "unknown_position", "future_or_unverified_state_omitted"}:
            return "partial"
        return "complete"

    def complete_invocation(
        self,
        *,
        transport: str,
        submitted_prompt_chars: int,
        command_chars: int,
        outcome: str,
        task_file_cleaned: bool,
        task_file_bytes: int = 0,
        file_ack_verified: bool = False,
        file_ack_retry_count: int = 0,
        file_ack_error: str = "",
        input_token_budget: int | None = None,
        runtime_reserve_tokens: int | None = None,
        estimated_input_tokens: int | None = None,
        token_estimator: str | None = None,
        model_context_window_tokens: int | None = None,
        context_strategy: str | None = None,
    ) -> "PromptContextReport":
        return replace(
            self,
            transport=str(transport or "unknown"),
            submitted_prompt_chars=max(0, int(submitted_prompt_chars)),
            command_chars=max(0, int(command_chars)),
            outcome=str(outcome or "unknown"),
            task_file_cleaned=bool(task_file_cleaned),
            task_file_bytes=max(0, int(task_file_bytes)),
            file_ack_verified=bool(file_ack_verified),
            file_ack_retry_count=max(0, int(file_ack_retry_count)),
            file_ack_error=str(file_ack_error or ""),
            input_token_budget=(
                self.input_token_budget
                if input_token_budget is None
                else max(0, int(input_token_budget))
            ),
            runtime_reserve_tokens=(
                self.runtime_reserve_tokens
                if runtime_reserve_tokens is None
                else max(0, int(runtime_reserve_tokens))
            ),
            estimated_input_tokens=(
                self.estimated_input_tokens
                if estimated_input_tokens is None
                else max(0, int(estimated_input_tokens))
            ),
            token_estimator=(
                self.token_estimator
                if token_estimator is None
                else str(token_estimator)
            ),
            model_context_window_tokens=(
                self.model_context_window_tokens
                if model_context_window_tokens is None
                else max(0, int(model_context_window_tokens))
            ),
            context_strategy=(
                self.context_strategy
                if context_strategy is None
                else str(context_strategy)
            ),
        )

    def to_dict(self) -> dict:
        """Return a JSON-safe report that cannot contain source prose."""
        value = asdict(self)
        value["health"] = self.health
        return value


@dataclass(frozen=True)
class PromptBundle:
    """Named prompt fields plus their redacted construction report."""

    system_prompt: str
    user_prompt: str
    report: PromptContextReport

ReportCallback = Callable[[PromptContextReport], None]
