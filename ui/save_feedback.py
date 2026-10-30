"""Read-only presentation of the document owner's real save state."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SaveFeedback:
    path: str | None
    state: str
    detail: str = ""

    @property
    def label(self) -> str:
        return {"empty": "", "clean": "已保存", "dirty": "未保存",
                "saving": "正在保存…", "error": "保存失败",
                "conflict": "外部版本变化"}[self.state]

    @property
    def needs_attention(self) -> bool:
        return self.state in {"error", "conflict"}


def save_feedback(path, *, dirty=False, saving=False, error="", conflict=False) -> SaveFeedback:
    """Never let a progress update conceal an unresolved failure or conflict."""
    if not path:
        return SaveFeedback(None, "empty")
    state = ("conflict" if conflict else "error" if error else "saving" if saving
             else "dirty" if dirty else "clean")
    return SaveFeedback(str(path), state, str(error or ""))
