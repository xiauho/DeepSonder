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
from core.length_policy import (
    LENGTH_SEVERELY_OVER,
    LENGTH_SEVERELY_UNDER,
    LENGTH_UNDER,
    assess_length,
    length_status_label,
)
from core.text_metrics import count_content_chars


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
        review_min_chars: int = 0,
        review_max_chars: int = 0,
        length_status: str = "qualified",
        supplement_attempt_count: int = 0,
        can_retry_supplement: bool = True,
        original_draft_text: str = "",
        original_draft_char_count: int = 0,
        correction_history: tuple[str, ...] = (),
        foreshadowing_feedback: tuple[ForeshadowingSuggestion, ...] = (),
        foreshadowing_titles: dict[str, str] | None = None,
        foreshadowing_warning: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.confirmed = False
        self.retry_requested = False
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
                f"本次目标：{target_chars} 字 · 理想范围：{min_chars}～{max_chars} 字"
            )
        if review_min_chars > 0 and review_max_chars > 0:
            details.append(f"审阅范围：{review_min_chars}～{review_max_chars} 字")
        if supplement_attempted:
            if supplement_added_chars > 0:
                details.append(
                    f"首次生成：{initial_char_count} 字 · 自动补写：{supplement_added_chars} 字"
                )
            else:
                details.append(f"首次生成：{initial_char_count} 字 · 自动补写未应用")
        details.append(
            f"最终正文：{char_count} 字 · "
            + length_status_label(length_status)
        )
        if supplement_attempt_count:
            details.append(f"差额补写已尝试 {supplement_attempt_count} 次。")
        if supplement_warning:
            details.append(supplement_warning)
        details.extend(correction_history)
        details.extend((notice, "未点击确认前，不会修改当前正文。"))
        info = QLabel("\n".join(details))
        info.setWordWrap(True)
        layout.addWidget(info)

        self._recommended_text = text
        self._candidate_text = text
        self._candidate_editor = QPlainTextEdit()
        self._candidate_editor.setReadOnly(True)
        self._candidate_editor.setPlainText(text)
        if original_draft_text:
            selector = QComboBox()
            selector.addItem(f"篇幅纠偏稿 · {char_count} 字（推荐）", text)
            selector.addItem(
                f"首次原稿 · {original_draft_char_count or count_content_chars(original_draft_text)} 字",
                original_draft_text,
            )
            selector.currentIndexChanged.connect(
                lambda: self._select_candidate(str(selector.currentData() or ""))
            )
            layout.addWidget(selector)
        layout.addWidget(self._candidate_editor, 1)

        if foreshadowing_titles:
            layout.addWidget(
                self._build_foreshadowing_feedback(
                    foreshadowing_feedback,
                    foreshadowing_titles,
                    foreshadowing_warning,
                )
            )

        buttons = QHBoxLayout()
        under_length = length_status in {LENGTH_SEVERELY_UNDER, LENGTH_UNDER}
        confirm = QPushButton("仍然采用" if under_length else confirm_label)
        confirm.setObjectName("accentButton")
        confirm.setAutoDefault(False)
        copy = QPushButton("复制")
        cancel = QPushButton("放弃")
        cancel.setAutoDefault(False)
        confirm.clicked.connect(self._confirm)
        copy.clicked.connect(lambda: self._copy(self._candidate_text))
        cancel.clicked.connect(self.reject)
        if under_length and can_retry_supplement:
            retry = QPushButton("重新补写")
            retry.setObjectName("accentButton")
            retry.setAutoDefault(False)
            retry.clicked.connect(self._retry)
            buttons.addWidget(retry)
            confirm.setObjectName("ghostButton")
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

    def _retry(self) -> None:
        self.retry_requested = True
        self.accept()

    def _select_candidate(self, text: str) -> None:
        self._candidate_text = text
        self._candidate_editor.setPlainText(text)

    def selected_text(self) -> str:
        return self._candidate_text

    def selected_resolution_ids(self) -> tuple[str, ...]:
        if self._candidate_text != self._recommended_text:
            return ()
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
        min_chars: int = 0,
        max_chars: int = 0,
        review_min_chars: int = 0,
        review_max_chars: int = 0,
        run_target_chars: int = 0,
        initial_generated_chars: int = 0,
        supplement_added_chars: int = 0,
        supplement_warning: str = "",
        length_status: str = "qualified",
        supplement_attempt_count: int = 0,
        can_retry_supplement: bool = True,
        original_draft_text: str = "",
        original_draft_char_count: int = 0,
        correction_history: tuple[str, ...] = (),
        parent=None,
    ):
        super().__init__(parent)
        self.confirmed = False
        self.retry_requested = False
        self.setWindowTitle("AI 续写结果预览")
        self.resize(780, 650)
        projected = current_chars + generated_chars
        effective_target = run_target_chars or target_chapter_chars
        difference = projected - effective_target
        if difference > 0:
            target_note = f"追加后预计超过本轮目标 {difference} 字，仅作为创作参考。"
        else:
            target_note = f"追加后距离本轮目标约 {abs(difference)} 字。"
        details = [
            f"目标章节：{target_chapter_chars} 字 · 本轮目标：{effective_target} 字 · 当前正文：{current_chars} 字",
            f"本次请求：{requested_chars} 字 · AI 实际生成：{generated_chars} 字 · "
            f"追加后预计：{projected} 字",
        ]
        if min_chars and max_chars:
            details.append(f"理想范围：{min_chars}～{max_chars} 字")
        if review_min_chars and review_max_chars:
            details.append(f"审阅范围：{review_min_chars}～{review_max_chars} 字")
        if initial_generated_chars and initial_generated_chars != generated_chars:
            details.append(
                f"首次生成：{initial_generated_chars} 字 · 差额补写：+{supplement_added_chars} 字"
            )
        if supplement_attempt_count:
            details.append(f"差额补写已尝试 {supplement_attempt_count} 次。")
        details.append(f"当前状态：{length_status_label(length_status)}。{target_note}")
        if supplement_warning:
            details.append(supplement_warning)
        details.extend(correction_history)
        details.append("未点击确认前，不会修改当前正文；确认后也不会自动保存。")
        info = QLabel("\n".join(details))
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
        self._candidate_text = text
        self._candidate_editor = QPlainTextEdit()
        self._candidate_editor.setReadOnly(True)
        self._candidate_editor.setPlainText(text)
        if original_draft_text:
            selector = QComboBox()
            selector.addItem(f"篇幅纠偏稿 · {generated_chars} 字（推荐）", text)
            selector.addItem(
                f"首次续写稿 · {original_draft_char_count or count_content_chars(original_draft_text)} 字",
                original_draft_text,
            )
            selector.currentIndexChanged.connect(
                lambda: self._select_candidate(str(selector.currentData() or ""))
            )
            layout.addWidget(selector)
        layout.addWidget(self._candidate_editor, 1)

        buttons = QHBoxLayout()
        under_length = length_status in {LENGTH_SEVERELY_UNDER, LENGTH_UNDER}
        confirm = QPushButton("仍然采用" if under_length else "追加到正文")
        confirm.setObjectName("accentButton")
        confirm.setAutoDefault(False)
        copy = QPushButton("复制")
        cancel = QPushButton("放弃")
        cancel.setAutoDefault(False)
        confirm.clicked.connect(self._confirm)
        copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self._candidate_text)
        )
        cancel.clicked.connect(self.reject)
        if under_length and can_retry_supplement:
            retry = QPushButton("重新补写")
            retry.setObjectName("accentButton")
            retry.setAutoDefault(False)
            retry.clicked.connect(self._retry)
            buttons.addWidget(retry)
            confirm.setObjectName("ghostButton")
        buttons.addWidget(confirm)
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def _confirm(self) -> None:
        self.confirmed = True
        self.accept()

    def _retry(self) -> None:
        self.retry_requested = True
        self.accept()

    def _select_candidate(self, text: str) -> None:
        self._candidate_text = text
        self._candidate_editor.setPlainText(text)

    def selected_text(self) -> str:
        return self._candidate_text


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
        review_min_chars: int = 0,
        review_max_chars: int = 0,
        length_status: str = "qualified",
        supplement_attempt_count: int = 0,
        can_retry_supplement: bool = True,
        original_draft_text: str = "",
        original_draft_char_count: int = 0,
        correction_history: tuple[str, ...] = (),
        foreshadowing_feedback: tuple[ForeshadowingSuggestion, ...] = (),
        foreshadowing_titles: dict[str, str] | None = None,
        foreshadowing_warning: str = "",
    ) -> CommitOutcome:
        dialog = ExpansionPreviewDialog(
            text=text,
            char_count=char_count,
            length_ok=length_ok,
            has_existing_content=has_existing_content,
            target_chars=target_chars,
            min_chars=min_chars,
            max_chars=max_chars,
            initial_char_count=initial_char_count,
            supplement_attempted=supplement_attempted,
            supplement_added_chars=supplement_added_chars,
            supplement_warning=supplement_warning,
            review_min_chars=review_min_chars,
            review_max_chars=review_max_chars,
            length_status=length_status,
            supplement_attempt_count=supplement_attempt_count,
            can_retry_supplement=can_retry_supplement,
            original_draft_text=original_draft_text,
            original_draft_char_count=original_draft_char_count,
            correction_history=correction_history,
            foreshadowing_feedback=foreshadowing_feedback,
            foreshadowing_titles=foreshadowing_titles,
            foreshadowing_warning=foreshadowing_warning,
            parent=self.parent,
        )
        dialog.exec()
        retry_requested = bool(getattr(dialog, "retry_requested", False))
        if not dialog.confirmed and not retry_requested:
            return CommitOutcome(status="cancelled")
        if not context_matches():
            QMessageBox.warning(
                self.parent,
                "章节已发生变化",
                "生成期间当前章节内容发生了变化，结果未自动写入。",
            )
            return CommitOutcome(status="stale")
        selected_text_getter = getattr(dialog, "selected_text", None)
        selected_text = (
            str(selected_text_getter())
            if callable(selected_text_getter)
            else text
        )
        selected_status = (
            assess_length(count_content_chars(selected_text), target_chars).status
            if target_chars > 0
            else length_status
        )
        if retry_requested:
            return CommitOutcome(status="supplement_requested", value=selected_text)
        if selected_status in {LENGTH_SEVERELY_UNDER, LENGTH_SEVERELY_OVER}:
            answer = QMessageBox.question(
                self.parent,
                "正文长度明显偏离目标",
                "当前正文超出审阅范围，是否仍然采用？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return CommitOutcome(status="cancelled")
        replace_body(selected_text)
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
        min_chars: int = 0,
        max_chars: int = 0,
        review_min_chars: int = 0,
        review_max_chars: int = 0,
        run_target_chars: int = 0,
        initial_generated_chars: int = 0,
        supplement_added_chars: int = 0,
        supplement_warning: str = "",
        length_status: str = "qualified",
        supplement_attempt_count: int = 0,
        can_retry_supplement: bool = True,
        original_draft_text: str = "",
        original_draft_char_count: int = 0,
        correction_history: tuple[str, ...] = (),
    ) -> CommitOutcome:
        dialog = ContinuationPreviewDialog(
            text=text,
            current_tail=current_tail,
            current_chars=current_chars,
            requested_chars=requested_chars,
            generated_chars=generated_chars,
            target_chapter_chars=target_chapter_chars,
            length_ok=length_ok,
            min_chars=min_chars,
            max_chars=max_chars,
            review_min_chars=review_min_chars,
            review_max_chars=review_max_chars,
            run_target_chars=run_target_chars,
            initial_generated_chars=initial_generated_chars,
            supplement_added_chars=supplement_added_chars,
            supplement_warning=supplement_warning,
            length_status=length_status,
            supplement_attempt_count=supplement_attempt_count,
            can_retry_supplement=can_retry_supplement,
            original_draft_text=original_draft_text,
            original_draft_char_count=original_draft_char_count,
            correction_history=correction_history,
            parent=self.parent,
        )
        dialog.exec()
        retry_requested = bool(getattr(dialog, "retry_requested", False))
        if not dialog.confirmed and not retry_requested:
            return CommitOutcome(status="cancelled")
        if not context_matches():
            QMessageBox.warning(
                self.parent,
                "章节已发生变化",
                "生成期间当前章节内容或相关资料发生了变化，续写结果未自动追加。",
            )
            return CommitOutcome(status="stale")
        selected_text_getter = getattr(dialog, "selected_text", None)
        selected_text = (
            str(selected_text_getter())
            if callable(selected_text_getter)
            else text
        )
        effective_target = run_target_chars or target_chapter_chars
        selected_status = assess_length(
            current_chars + count_content_chars(selected_text),
            effective_target,
        ).status
        if retry_requested:
            return CommitOutcome(status="supplement_requested", value=selected_text)
        if selected_status in {LENGTH_SEVERELY_UNDER, LENGTH_SEVERELY_OVER}:
            answer = QMessageBox.question(
                self.parent,
                "正文长度明显偏离目标",
                "追加后的正文超出审阅范围，是否仍然采用？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return CommitOutcome(status="cancelled")
        append_body(selected_text)
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
