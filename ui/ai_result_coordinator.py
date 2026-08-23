"""Preview, confirmation and stale-context guards for AI results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout


class ExpansionPreviewDialog(QDialog):
    """Preview a draft and require one explicit write decision."""

    def __init__(self, text: str, char_count: int, length_ok: bool, has_existing_content: bool, parent=None):
        super().__init__(parent)
        self.confirmed = False
        self.setWindowTitle("扩写结果预览")
        self.resize(760, 560)

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
    ) -> CommitOutcome:
        dialog = ExpansionPreviewDialog(
            text,
            char_count,
            length_ok,
            has_existing_content,
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
        return CommitOutcome(
            status="committed",
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
