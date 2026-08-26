"""Preview, confirmation and stale-context guards for AI results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.ai_protocol import ForeshadowingSuggestion


@dataclass(frozen=True)
class ForeshadowingReviewRow:
    note_id: str
    title: str
    suggestion: ForeshadowingSuggestion | None


def build_foreshadowing_review_rows(
    feedback: tuple[ForeshadowingSuggestion, ...],
    titles: dict[str, str],
) -> tuple[ForeshadowingReviewRow, ...]:
    """Keep every author-selected note visible, even when AI misses it."""
    suggestions = {item.foreshadowing_id: item for item in feedback}
    return tuple(
        ForeshadowingReviewRow(
            note_id=note_id,
            title=title or note_id,
            suggestion=suggestions.get(note_id),
        )
        for note_id, title in titles.items()
        if note_id
    )


class ExpansionPreviewDialog(QDialog):
    """Preview a draft and require one explicit write decision."""

    def __init__(
        self,
        text: str,
        char_count: int,
        length_ok: bool,
        has_existing_content: bool,
        foreshadowing_feedback: tuple[ForeshadowingSuggestion, ...] = (),
        foreshadowing_titles: dict[str, str] | None = None,
        foreshadowing_warning: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.confirmed = False
        self._foreshadowing_selectors: dict[str, QComboBox] = {}
        self.setWindowTitle("扩写结果预览")
        self.resize(780, 680 if foreshadowing_titles else 560)

        layout = QVBoxLayout(self)
        if has_existing_content:
            notice = "当前章节已有正文，确认后将替换正文。"
            confirm_label = "确认替换正文"
        else:
            notice = "当前章节正文为空，确认后将写入生成结果。"
            confirm_label = "确认写入正文"
        info = QLabel(
            f"已生成约 {char_count} 字。"
            + ("长度在目标范围内。" if length_ok else "长度超出目标范围，请审阅后决定。")
            + f"\n{notice}\n未点击确认前，不会修改当前正文。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        layout.addWidget(editor, 1)

        if foreshadowing_titles:
            layout.addWidget(
                self._build_foreshadowing_feedback(
                    foreshadowing_feedback,
                    foreshadowing_titles,
                    foreshadowing_warning,
                )
            )

        buttons = QHBoxLayout()
        confirm = QPushButton(confirm_label)
        confirm.setObjectName("accentButton")
        confirm.setAutoDefault(False)
        copy = QPushButton("复制")
        cancel = QPushButton("放弃")
        cancel.setAutoDefault(False)
        confirm.clicked.connect(self._confirm)
        copy.clicked.connect(lambda: self._copy(text))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(confirm)
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)

    def _confirm(self) -> None:
        self.confirmed = True
        self.accept()

    def selected_resolution_ids(self) -> tuple[str, ...]:
        return tuple(
            note_id
            for note_id, selector in self._foreshadowing_selectors.items()
            if selector.currentData() == "resolved"
        )

    def _build_foreshadowing_feedback(
        self,
        feedback: tuple[ForeshadowingSuggestion, ...],
        titles: dict[str, str],
        warning: str,
    ) -> QGroupBox:
        group = QGroupBox("本次选择的伏笔（AI 反馈仅供参考）")
        group_layout = QVBoxLayout(group)
        hint = QLabel("默认保持未回收；无论 AI 是否命中，你都可以自行切换，选择将在章节保存成功后生效。")
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        group_layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(230)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        for row in build_foreshadowing_review_rows(feedback, titles):
            note_id = row.note_id
            suggestion = row.suggestion
            title_label = QLabel(row.title)
            title_label.setObjectName("sectionTitle")
            content_layout.addWidget(title_label)
            if suggestion is not None:
                detail_text = (
                    "AI 判断：可能已回收\n"
                    f"正文依据：{suggestion.evidence}\n"
                    f"判断理由：{suggestion.reason}"
                )
            elif warning:
                detail_text = f"AI 判断：本次复核未提供有效建议。\n{warning}"
            else:
                detail_text = "AI 判断：未识别为已回收。你仍可根据正文自行判断。"
            detail = QLabel(detail_text)
            detail.setWordWrap(True)
            detail.setObjectName("mutedLabel")
            content_layout.addWidget(detail)
            selector = QComboBox()
            selector.addItem("保持未回收", "open")
            selector.addItem("标记为已回收", "resolved")
            self._foreshadowing_selectors[note_id] = selector
            content_layout.addWidget(selector)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        group_layout.addWidget(scroll)
        return group


@dataclass(frozen=True)
class CommitOutcome:
    status: str
    value: object | None = None
    action: str | None = None
    error: str | None = None


class AIResultCoordinator:
    """Own all user confirmation dialogs and commit guards for AI results."""

    def __init__(self, parent=None):
        self.parent = parent

    def confirm_expansion(
        self,
        *,
        text: str,
        char_count: int,
        length_ok: bool,
        has_existing_content: bool,
        context_matches: Callable[[], bool],
        replace_body: Callable[[str], None],
        foreshadowing_feedback: tuple[ForeshadowingSuggestion, ...] = (),
        foreshadowing_titles: dict[str, str] | None = None,
        foreshadowing_warning: str = "",
    ) -> CommitOutcome:
        dialog = ExpansionPreviewDialog(
            text,
            char_count,
            length_ok,
            has_existing_content,
            foreshadowing_feedback,
            foreshadowing_titles,
            foreshadowing_warning,
            self.parent,
        )
        dialog.exec()
        if not dialog.confirmed:
            return CommitOutcome(status="cancelled")
        if not context_matches():
            QMessageBox.warning(
                self.parent,
                "章节已发生变化",
                "生成期间当前章节内容发生了变化，结果未自动写入。",
            )
            return CommitOutcome(status="stale")
        replace_body(text)
        selected_resolutions = getattr(dialog, "selected_resolution_ids", None)
        resolution_ids = (
            tuple(selected_resolutions()) if callable(selected_resolutions) else ()
        )
        return CommitOutcome(
            status="committed",
            value=resolution_ids,
            action="替换" if has_existing_content else "写入",
        )

    def confirm_memory(
        self,
        *,
        summary: str,
        context_matches: Callable[[], bool],
        commit: Callable[[], object],
    ) -> CommitOutcome:
        answer = QMessageBox.question(
            self.parent,
            "确认更新长期记忆",
            f"章节摘要：\n{summary}\n\n确认写入章节摘要和故事状态吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return CommitOutcome(status="cancelled")
        if not context_matches():
            QMessageBox.warning(
                self.parent,
                "项目内容已变化",
                "生成期间项目或当前章节发生了变化，记忆结果未写入。",
            )
            return CommitOutcome(status="stale")
        try:
            value = commit()
        except Exception as exc:  # noqa: BLE001 - returned to the UI layer
            return CommitOutcome(status="failed", error=str(exc))
        return CommitOutcome(status="committed", value=value)
