"""Selection and field-level preview dialogs for character-card synchronization."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.character_card_sync import CharacterCardSyncProposal, display_value
from core.project import NovelProject


class CharacterCardSyncSelectionDialog(QDialog):
    """Choose one character and one contiguous chapter range."""

    def __init__(
        self,
        project: NovelProject,
        *,
        default_card: Path | str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("同步角色档案")
        self.resize(560, 300)
        self._cards = project.list_characters()
        self._chapters = project.list_chapters()

        layout = QVBoxLayout(self)
        intro = QLabel(
            "选择一个角色和连续章节范围。同步只使用已经采用且版本有效的章节记忆，"
            "并以截止章节结束后的状态生成字段级预览。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("mutedLabel")
        layout.addWidget(intro)

        form = QFormLayout()
        self.character = QComboBox()
        for path in self._cards:
            self.character.addItem(path.stem, str(path))
        self.start_chapter = QComboBox()
        self.end_chapter = QComboBox()
        for path in self._chapters:
            try:
                title = project.load_chapter(path.stem).title or path.stem
            except (OSError, UnicodeError, ValueError):
                title = path.stem
            label = f"{title}  ·  {path.stem}"
            self.start_chapter.addItem(label, path.stem)
            self.end_chapter.addItem(label, path.stem)
        if self._chapters:
            self.start_chapter.setCurrentIndex(len(self._chapters) - 1)
            self.end_chapter.setCurrentIndex(len(self._chapters) - 1)
        if default_card:
            target = str(Path(default_card).resolve()).casefold()
            for index in range(self.character.count()):
                if str(Path(self.character.itemData(index)).resolve()).casefold() == target:
                    self.character.setCurrentIndex(index)
                    break
        self.start_chapter.currentIndexChanged.connect(self._keep_range_valid)
        self.end_chapter.currentIndexChanged.connect(self._keep_range_valid)
        form.addRow("角色", self.character)
        form.addRow("起始章节", self.start_chapter)
        form.addRow("截止章节", self.end_chapter)
        layout.addLayout(form)

        note = QLabel(
            "位置、状态、战力、物品和关系可以进入同步预览；身份、性别、年龄、秘密和人物弧线不会自动修改。"
        )
        note.setWordWrap(True)
        note.setObjectName("mutedLabel")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("生成同步预览")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(self._cards and self._chapters)
        )
        layout.addWidget(buttons)

    def selection(self) -> tuple[Path, tuple[str, ...]] | None:
        if self.character.currentIndex() < 0 or self.start_chapter.currentIndex() < 0:
            return None
        start = self.start_chapter.currentIndex()
        end = self.end_chapter.currentIndex()
        if start > end:
            return None
        card = Path(str(self.character.currentData()))
        chapters = tuple(path.stem for path in self._chapters[start : end + 1])
        return card, chapters

    def _keep_range_valid(self, _index: int) -> None:
        if self.start_chapter.currentIndex() > self.end_chapter.currentIndex():
            sender = self.sender()
            if sender is self.start_chapter:
                self.end_chapter.setCurrentIndex(self.start_chapter.currentIndex())
            else:
                self.start_chapter.setCurrentIndex(self.end_chapter.currentIndex())


class CharacterCardSyncPreviewDialog(QDialog):
    """Preview and independently accept evidence-bound field patches."""

    def __init__(self, proposal: CharacterCardSyncProposal, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("角色档案同步预览")
        self.resize(760, 650)
        self._checks: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        info = QLabel(
            f"角色：{proposal.character_name}\n"
            f"章节范围：{proposal.chapter_ids[0]} ～ {proposal.chapter_ids[-1]}\n"
            f"同步结果表示 {proposal.as_of_chapter} 结束后的状态。请逐项核对正文证据。"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        for patch in proposal.patches:
            check = QCheckBox(
                f"{patch.label}：{display_value(patch.expected_before)}  →  {display_value(patch.value)}"
            )
            check.setChecked(patch.default_selected)
            check.setToolTip(
                "正文明确事实，默认选中" if patch.default_selected else "推断事实，默认不选中，请人工确认"
            )
            self._checks[patch.field] = check
            body_layout.addWidget(check)

            evidence = QPlainTextEdit()
            evidence.setReadOnly(True)
            evidence.setMaximumHeight(115)
            evidence.setPlainText(
                "\n".join(
                    f"[{item.chapter_id} · {item.certainty} · {item.fact_id or '无事实 ID'}] {item.quote}"
                    for item in patch.evidence
                )
            )
            body_layout.addWidget(evidence)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        warning = QLabel(
            "确认后仅更新角色卡中的“DeepSonder 同步”标记区；其他作者内容保持不变。"
            "写入前会再次检查角色卡版本，并在项目备份目录保存原文。"
        )
        warning.setWordWrap(True)
        warning.setObjectName("mutedLabel")
        layout.addWidget(warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._apply_button.setText("应用所选变更")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("放弃")
        buttons.accepted.connect(self._accept_if_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for check in self._checks.values():
            check.toggled.connect(self._update_apply_enabled)
        self._update_apply_enabled()

    def selected_fields(self) -> tuple[str, ...]:
        return tuple(field for field, check in self._checks.items() if check.isChecked())

    def _accept_if_selected(self) -> None:
        if not self.selected_fields():
            return
        self.accept()

    def _update_apply_enabled(self, _checked: bool | None = None) -> None:
        self._apply_button.setEnabled(bool(self.selected_fields()))
