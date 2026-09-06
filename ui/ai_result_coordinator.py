"""Preview, confirmation and stale-context guards for AI results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
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
        target_chars: int = 0,
        min_chars: int = 0,
        max_chars: int = 0,
        initial_char_count: int = 0,
        supplement_attempted: bool = False,
        supplement_added_chars: int = 0,
        supplement_warning: str = "",
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
        details = []
        if target_chars > 0:
            details.append(
                f"本次目标：{target_chars} 字 · 允许范围：{min_chars}～{max_chars} 字"
            )
        if supplement_attempted:
            if supplement_added_chars > 0:
                details.append(
                    f"首次生成：{initial_char_count} 字 · 自动补写：{supplement_added_chars} 字"
                )
            else:
                details.append(f"首次生成：{initial_char_count} 字 · 自动补写未应用")
        details.append(
            f"最终正文：{char_count} 字 · "
            + ("长度在目标范围内。" if length_ok else "长度超出目标范围，请审阅后决定。")
        )
        if supplement_warning:
            details.append(supplement_warning)
        details.extend((notice, "未点击确认前，不会修改当前正文。"))
        info = QLabel("\n".join(details))
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


class ContinuationPreviewDialog(QDialog):
    """Preview a continuation together with the seam it will follow."""

    def __init__(
        self,
        text: str,
        current_tail: str,
        current_chars: int,
        requested_chars: int,
        generated_chars: int,
        target_chapter_chars: int,
        length_ok: bool,
        parent=None,
    ):
        super().__init__(parent)
        self.confirmed = False
        self.setWindowTitle("AI 续写结果预览")
        self.resize(780, 650)
        projected = current_chars + generated_chars
        difference = projected - target_chapter_chars
        if difference > 0:
            target_note = f"追加后预计超过目标 {difference} 字，仅作为创作参考。"
        else:
            target_note = f"追加后距离目标约 {abs(difference)} 字。"
        info = QLabel(
            f"目标章节：{target_chapter_chars} 字 · 当前正文：{current_chars} 字\n"
            f"本次请求：{requested_chars} 字 · AI 实际生成：{generated_chars} 字 · "
            f"追加后预计：{projected} 字\n"
            + ("生成长度在参考范围内。" if length_ok else "生成长度超出参考范围，请审阅后决定。")
            + f"{target_note}\n未点击确认前，不会修改当前正文；确认后也不会自动保存。"
        )
        info.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        seam_label = QLabel("当前正文结尾")
        seam_label.setObjectName("sectionTitle")
        layout.addWidget(seam_label)
        seam = QPlainTextEdit()
        seam.setReadOnly(True)
        seam.setPlainText(current_tail)
        seam.setMaximumHeight(150)
        layout.addWidget(seam)
        generated_label = QLabel("AI 续写内容")
        generated_label.setObjectName("sectionTitle")
        layout.addWidget(generated_label)
        generated = QPlainTextEdit()
        generated.setReadOnly(True)
        generated.setPlainText(text)
        layout.addWidget(generated, 1)

        buttons = QHBoxLayout()
        confirm = QPushButton("追加到正文")
        confirm.setObjectName("accentButton")
        confirm.setAutoDefault(False)
        copy = QPushButton("复制")
        cancel = QPushButton("放弃")
        cancel.setAutoDefault(False)
        confirm.clicked.connect(self._confirm)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(confirm)
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _confirm(self) -> None:
        self.confirmed = True
        self.accept()


class RepairPreviewDialog(QDialog):
    """Preview one contiguous replacement before it touches the editor."""

    def __init__(
        self,
        expected_original: str,
        replacement: str,
        explanation: str,
        preserved_facts: tuple[str, ...] = (),
        parent=None,
    ):
        super().__init__(parent)
        self.confirmed = False
        self.setWindowTitle("AI 修复预览")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "仅会替换下面这一处连续正文。确认前不会修改编辑器，也不会自动保存。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        for title, text in (("原文", expected_original), ("修复后", replacement)):
            label = QLabel(title)
            label.setObjectName("sectionTitle")
            layout.addWidget(label)
            editor = QPlainTextEdit()
            editor.setReadOnly(True)
            editor.setPlainText(text)
            editor.setMaximumHeight(180)
            layout.addWidget(editor)
        detail = QLabel("修复说明：" + explanation)
        detail.setWordWrap(True)
        detail.setObjectName("mutedLabel")
        layout.addWidget(detail)
        if preserved_facts:
            facts = QLabel("保留事实：" + "；".join(preserved_facts))
            facts.setWordWrap(True)
            facts.setObjectName("mutedLabel")
            layout.addWidget(facts)
        buttons = QHBoxLayout()
        confirm = QPushButton("确认应用")
        confirm.setObjectName("accentButton")
        confirm.setAutoDefault(False)
        cancel = QPushButton("放弃")
        cancel.setAutoDefault(False)
        confirm.clicked.connect(self._confirm)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(confirm)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _confirm(self) -> None:
        self.confirmed = True
        self.accept()


class MemoryPreviewDialog(QDialog):
    """Keep long memory proposals reviewable without growing off screen."""

    def __init__(
        self,
        summary: str,
        details: str = "",
        patch_count: int = 0,
        conflict_count: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self.confirmed = False
        self.setObjectName("memoryPreviewDialog")
        self.setWindowTitle("确认更新长期记忆")
        self.setSizeGripEnabled(True)

        screen = QApplication.primaryScreen()
        if screen is None:
            width, height = 780, 650
        else:
            available = screen.availableGeometry()
            width = min(780, max(520, int(available.width() * 0.75)))
            height = min(650, max(420, int(available.height() * 0.75)))
        self.setMinimumSize(min(620, width), min(480, height))
        self.resize(width, height)

        layout = QVBoxLayout(self)
        heading = QLabel("请审阅本次记忆更新，确认前不会修改项目数据。")
        heading.setWordWrap(True)
        layout.addWidget(heading)

        counts = QLabel(
            f"{max(0, patch_count)} 条状态变更 · "
            f"{max(0, conflict_count)} 条冲突或警告"
        )
        counts.setObjectName("mutedLabel")
        layout.addWidget(counts)

        self.preview = QPlainTextEdit()
        self.preview.setObjectName("memoryPreviewText")
        self.preview.setReadOnly(True)
        self.preview.setTabChangesFocus(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.preview.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.preview.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        summary_text = str(summary or "").strip() or "（无）"
        details_text = str(details or "").strip()
        content = f"章节摘要：\n{summary_text}"
        if details_text:
            content += f"\n\n{details_text}"
        else:
            content += "\n\n状态变更：\n- 无"
        self.preview.setPlainText(content)
        layout.addWidget(self.preview, 1)

        hint = QLabel("确认后将写入章节摘要和故事状态。")
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        copy = QPushButton("复制全部")
        cancel = QPushButton("取消")
        confirm = QPushButton("确认写入")
        confirm.setObjectName("accentButton")
        confirm.setAutoDefault(False)
        cancel.setAutoDefault(False)
        copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.preview.toPlainText())
        )
        cancel.clicked.connect(self.reject)
        confirm.clicked.connect(self._confirm)
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)

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
        target_chars: int = 0,
        min_chars: int = 0,
        max_chars: int = 0,
        initial_char_count: int = 0,
        supplement_attempted: bool = False,
        supplement_added_chars: int = 0,
        supplement_warning: str = "",
        foreshadowing_feedback: tuple[ForeshadowingSuggestion, ...] = (),
        foreshadowing_titles: dict[str, str] | None = None,
        foreshadowing_warning: str = "",
    ) -> CommitOutcome:
        dialog = ExpansionPreviewDialog(
            text,
            char_count,
            length_ok,
            has_existing_content,
            target_chars,
            min_chars,
            max_chars,
            initial_char_count,
            supplement_attempted,
            supplement_added_chars,
            supplement_warning,
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

    def confirm_continuation(
        self,
        *,
        text: str,
        current_tail: str,
        current_chars: int,
        requested_chars: int,
        generated_chars: int,
        target_chapter_chars: int,
        length_ok: bool,
        context_matches: Callable[[], bool],
        append_body: Callable[[str], None],
    ) -> CommitOutcome:
        dialog = ContinuationPreviewDialog(
            text,
            current_tail,
            current_chars,
            requested_chars,
            generated_chars,
            target_chapter_chars,
            length_ok,
            self.parent,
        )
        dialog.exec()
        if not dialog.confirmed:
            return CommitOutcome(status="cancelled")
        if not context_matches():
            QMessageBox.warning(
                self.parent,
                "章节已发生变化",
                "生成期间当前章节内容或相关资料发生了变化，续写结果未自动追加。",
            )
            return CommitOutcome(status="stale")
        append_body(text)
        return CommitOutcome(status="committed", action="追加")

    def confirm_memory(
        self,
        *,
        summary: str,
        details: str = "",
        patch_count: int = 0,
        conflict_count: int = 0,
        context_matches: Callable[[], bool],
        commit: Callable[[], object],
    ) -> CommitOutcome:
        dialog = MemoryPreviewDialog(
            summary,
            details,
            patch_count,
            conflict_count,
            self.parent,
        )
        dialog.exec()
        if not dialog.confirmed:
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

    def confirm_repair(
        self,
        *,
        expected_original: str,
        replacement: str,
        explanation: str,
        preserved_facts: tuple[str, ...],
        context_matches: Callable[[], bool],
        apply_replacement: Callable[[], bool],
    ) -> CommitOutcome:
        dialog = RepairPreviewDialog(
            expected_original,
            replacement,
            explanation,
            preserved_facts,
            self.parent,
        )
        dialog.exec()
        if not dialog.confirmed:
            return CommitOutcome(status="cancelled")
        if not context_matches():
            QMessageBox.warning(
                self.parent,
                "章节已发生变化",
                "生成期间当前章节内容发生了变化，修复未自动写入。",
            )
            return CommitOutcome(status="stale")
        try:
            applied = bool(apply_replacement())
        except Exception as exc:  # noqa: BLE001 - returned to the UI layer
            return CommitOutcome(status="failed", error=str(exc))
        if not applied:
            return CommitOutcome(status="failed", error="正文原句已变化，无法安全替换。")
        return CommitOutcome(status="committed", action="替换")
