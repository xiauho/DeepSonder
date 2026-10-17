"""Describe the active workspace document independently of its editor widget."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DocumentContext:
    path: Path | None
    category: str = ""
    structured: bool = False
    chapter_id: str | None = None

    @property
    def route(self) -> str:
        return "writing" if self.category == "章节" else "canon"
