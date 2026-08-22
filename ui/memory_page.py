from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.project import NovelProject
from ui.icons import IconTextButton


class ClickableCard(QFrame):
    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StoryMemoryPage(QWidget):
    """Project-level story memory dashboard inside the shared application shell."""

    sync_requested = Signal()
    chapter_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pageSurface")
        self._project: NovelProject | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll)

        content = QWidget()
        content.setObjectName("pageContent")
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(30, 26, 34, 30)
        self.content_layout.setSpacing(18)
        scroll.setWidget(content)

        header = QFrame()
        header.setObjectName("pageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        eyebrow = QLabel("MEMORY / CONTINUITY")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("故事记忆")
        title.setObjectName("pageTitle")
        subtitle = QLabel("查看章节摘要、当前状态和仍待回收的故事线索。")
        subtitle.setObjectName("mutedLabel")
        title_box.addWidget(eyebrow)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header_layout.addLayout(title_box, 1)
        self.sync_button = IconTextButton("sync", "同步上下文", centered=True)
        self.sync_button.setObjectName("accentButton")
        self.sync_button.clicked.connect(self.sync_requested)
        header_layout.addWidget(self.sync_button, 0, Qt.AlignmentFlag.AlignTop)
        self.content_layout.addWidget(header)

        self.metrics = QGridLayout()
        self.metrics.setHorizontalSpacing(12)
        self.metric_values: list[QLabel] = []
        for index, caption in enumerate(("已摘要章节", "追踪角色", "未回收伏笔", "上下文健康度")):
            card = QFrame()
            card.setObjectName("statCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(15, 13, 15, 13)
            value = QLabel("—")
            value.setObjectName("statValue")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption_label = QLabel(caption)
            caption_label.setObjectName("statCaption")
            caption_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card_layout.addWidget(value)
            card_layout.addWidget(caption_label)
            self.metric_values.append(value)
            self.metrics.addWidget(card, 0, index)
        self.content_layout.addLayout(self.metrics)

        columns = QHBoxLayout()
        columns.setSpacing(14)
        self.summary_section, self.summary_layout = self._section("章节摘要")
        self.state_section, self.state_layout = self._section("当前状态")
        columns.addWidget(self.summary_section, 2)
        columns.addWidget(self.state_section, 1)
        self.content_layout.addLayout(columns)
        self.content_layout.addStretch(1)
        self.show_project(None)

    @staticmethod
    def _section(title: str) -> tuple[QFrame, QVBoxLayout]:
        section = QFrame()
        section.setObjectName("sectionCard")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(17, 15, 17, 17)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        return section, layout

    @staticmethod
    def _clear_after_heading(layout: QVBoxLayout) -> None:
        while layout.count() > 1:
            item = layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def show_project(self, project: NovelProject | None) -> None:
        self._project = project
        self._clear_after_heading(self.summary_layout)
        self._clear_after_heading(self.state_layout)
        if project is None:
            for value in self.metric_values:
                value.setText("—")
            self.sync_button.setEnabled(False)
            self.summary_layout.addWidget(self._empty_card("尚未打开项目", "打开项目后，这里会整理章节摘要。"))
            self.state_layout.addWidget(self._empty_card("等待故事状态", "当前位置、角色状态和伏笔会显示在这里。"))
            return

        self.sync_button.setEnabled(bool(project.list_chapters()))
        state = project.load_story_state()
        summaries = project.load_chapter_summaries()
        characters = state.get("characters") or {}
        hooks = state.get("foreshadowing") or []
        health = self._context_health(state, summaries)
        for label, value in zip(
            self.metric_values,
            (str(len(summaries)), str(len(characters)), str(len(hooks)), f"{health}%"),
        ):
            label.setText(value)

        if summaries:
            for chapter_id, summary in sorted(
                summaries.items(), key=lambda item: self._chapter_number(item[0]), reverse=True
            ):
                self.summary_layout.addWidget(self._summary_card(chapter_id, str(summary), project))
        else:
            self.summary_layout.addWidget(
                self._empty_card("还没有章节摘要", "完成一章后点击“同步上下文”，沉淀本章的关键进展。")
            )
        self.summary_layout.addStretch(1)

        self.state_layout.addWidget(self._state_card(state))
        hooks_title = QLabel("未回收伏笔")
        hooks_title.setObjectName("sectionTitle")
        self.state_layout.addWidget(hooks_title)
        if hooks:
            for index, hook in enumerate(hooks):
                self.state_layout.addWidget(self._hook_card(str(hook), index))
        else:
            self.state_layout.addWidget(self._empty_card("暂无未回收伏笔", "新的线索会在同步上下文后出现在这里。"))
        self.state_layout.addStretch(1)

    def set_syncing(self, syncing: bool) -> None:
        self.sync_button.setEnabled(not syncing and self._project is not None)
        self.sync_button.set_label("正在同步…" if syncing else "同步上下文")

    def _summary_card(self, chapter_id: str, summary: str, project: NovelProject) -> QWidget:
        card = ClickableCard()
        card.setObjectName("sectionCard")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.clicked.connect(lambda cid=chapter_id: self.chapter_requested.emit(cid))
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)
        chapter = project.load_chapter(chapter_id)
        title_row = QHBoxLayout()
        title = QLabel(chapter.title)
        title.setObjectName("sectionTitle")
        number = QLabel(chapter_id.replace("chapter_", "第") + "章")
        number.setObjectName("mutedLabel")
        title_row.addWidget(title, 1)
        title_row.addWidget(number)
        body = QLabel(summary)
        body.setObjectName("mutedLabel")
        body.setWordWrap(True)
        layout.addLayout(title_row)
        layout.addWidget(body)
        return card

    def _state_card(self, state: dict) -> QWidget:
        card = QFrame()
        card.setObjectName("sectionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 13, 14, 13)
        layout.setSpacing(8)
        layout.addWidget(self._info_row("当前位置", str(state.get("current_location") or "尚未记录")))
        layout.addWidget(self._info_row("故事时间", str(state.get("current_time") or state.get("story_time") or "尚未记录")))
        active = QLabel("活跃角色")
        active.setObjectName("statCaption")
        layout.addWidget(active)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        characters = state.get("characters") or {}
        if characters:
            for name, details in list(characters.items())[:6]:
                chip = QLabel(str(name))
                chip.setObjectName("dirtyBadge" if isinstance(details, dict) and "伤" in str(details.get("state", "")) else "savedBadge")
                chip.setToolTip(str(details.get("state", "")) if isinstance(details, dict) else "")
                chips.addWidget(chip)
        else:
            empty = QLabel("暂无追踪角色")
            empty.setObjectName("mutedLabel")
            chips.addWidget(empty)
        chips.addStretch(1)
        layout.addLayout(chips)
        return card

    @staticmethod
    def _info_row(label: str, value: str) -> QWidget:
        row = QWidget()
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        caption = QLabel(label)
        caption.setObjectName("statCaption")
        value_label = QLabel(value)
        value_label.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(value_label)
        return row

    @staticmethod
    def _hook_card(text: str, index: int) -> QWidget:
        card = QFrame()
        card.setObjectName("sectionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        title = QLabel(text)
        title.setWordWrap(True)
        title.setObjectName("sectionTitle")
        hint = QLabel(f"线索 {index + 1} · 仍待回收")
        hint.setObjectName("mutedLabel")
        layout.addWidget(title)
        layout.addWidget(hint)
        return card

    @staticmethod
    def _empty_card(title: str, body: str) -> QWidget:
        card = QFrame()
        card.setObjectName("emptyState")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(17, 18, 17, 18)
        heading = QLabel(title)
        heading.setObjectName("sectionTitle")
        text = QLabel(body)
        text.setObjectName("mutedLabel")
        text.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(text)
        return card

    @staticmethod
    def _chapter_number(chapter_id: str) -> int:
        match = re.search(r"(\d+)", chapter_id)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _context_health(state: dict, summaries: dict) -> int:
        checks = (
            bool(summaries),
            bool(state.get("current_location")),
            bool(state.get("characters")),
            bool(state.get("current_chapter")),
        )
        return round(sum(checks) / len(checks) * 100)
