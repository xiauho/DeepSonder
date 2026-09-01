"""Redacted diagnostics for one AI prompt and its DSH transport."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Callable, Iterator


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
    history_requested: int | None = None
    history_available: int | None = None
    history_included: int | None = None
    transport: str = "pending"
    submitted_prompt_chars: int = 0
    command_chars: int = 0
    fallback_used: bool = False
    outcome: str = "pending"
    task_file_cleaned: bool = True

    @property
    def health(self) -> str:
        """Return a stable UI severity derived only from allocation states."""
        if any(item.status == "dropped" and item.priority <= 2 for item in self.sections):
            return "critical"
        if any(item.status in {"capped", "trimmed", "dropped"} for item in self.sections):
            return "partial"
        return "complete"

    def complete_invocation(
        self,
        *,
        transport: str,
        submitted_prompt_chars: int,
        command_chars: int,
        fallback_used: bool,
        outcome: str,
        task_file_cleaned: bool,
    ) -> "PromptContextReport":
        return replace(
            self,
            transport=str(transport or "unknown"),
            submitted_prompt_chars=max(0, int(submitted_prompt_chars)),
            command_chars=max(0, int(command_chars)),
            fallback_used=bool(fallback_used),
            outcome=str(outcome or "unknown"),
            task_file_cleaned=bool(task_file_cleaned),
        )

    def to_dict(self) -> dict:
        """Return a JSON-safe report that cannot contain source prose."""
        value = asdict(self)
        value["health"] = self.health
        return value


@dataclass(frozen=True)
class PromptBundle:
    """Tuple-compatible prompts plus their redacted construction report."""

    system_prompt: str
    user_prompt: str
    report: PromptContextReport

    def __iter__(self) -> Iterator[str]:
        yield self.system_prompt
        yield self.user_prompt

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int) -> str:
        return (self.system_prompt, self.user_prompt)[index]


ReportCallback = Callable[[PromptContextReport], None]
