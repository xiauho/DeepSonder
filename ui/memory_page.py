from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.project import NovelProject
from core.project import chapter_number_from_id
from core.project_data import ProjectDataStore
from ui.foreshadowing_dialog import ForeshadowingEditorDialog
from ui.icons import IconTextButton, set_button_icon


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
    foreshadowing_changed = Signal()

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
        store = ProjectDataStore(project)
        state = store.load_story_state()
        summaries = store.load_chapter_summaries()
        characters = state.get("characters") or {}
        try:
            notes = store.load_foreshadowing()
            note_error = ""
        except (OSError, ValueError) as exc:
            notes = []
            note_error = str(exc)
        open_notes = [note for note in notes if note.get("status") == "open"]
        health = self._context_health(state, summaries)
        for label, value in zip(
            self.metric_values,
            (str(len(summaries)), str(len(characters)), str(len(open_notes)), f"{health}%"),
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
        notes_header_widget = QWidget()
        notes_header = QHBoxLayout(notes_header_widget)
        notes_header.setContentsMargins(0, 0, 0, 0)
        notes_title = QLabel("伏笔笔记")
        notes_title.setObjectName("sectionTitle")
        notes_header.addWidget(notes_title, 1)
        add_button = IconTextButton("add", "新建伏笔", centered=True)
        add_button.setObjectName("secondaryButton")
        add_button.clicked.connect(self._new_foreshadowing)
        notes_header.addWidget(add_button)
        self.state_layout.addWidget(notes_header_widget)
        if note_error:
            self.state_layout.addWidget(self._empty_card("伏笔笔记读取失败", note_error))
        elif notes:
            for note in sorted(notes, key=self._foreshadowing_sort_key):
                self.state_layout.addWidget(self._foreshadowing_card(note))
        else:
            self.state_layout.addWidget(
                self._empty_card("暂无伏笔笔记", "可以手动记录需要长期追踪的故事线索。")
            )
        self.state_layout.addStretch(1)

    def _new_foreshadowing(self) -> None:
        dialog = ForeshadowingEditorDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            ProjectDataStore(self._project).create_foreshadowing(**dialog.values())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存伏笔失败", str(exc))
            return
        self.foreshadowing_changed.emit()

    def _edit_foreshadowing(self, note: dict) -> None:
        dialog = ForeshadowingEditorDialog(note, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            ProjectDataStore(self._project).update_foreshadowing(
                str(note.get("id") or ""), **dialog.values()
            )
        except (KeyError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存伏笔失败", str(exc))
            return
        self.foreshadowing_changed.emit()

    def _delete_foreshadowing(self, note: dict) -> None:
        title = str(note.get("title") or "该伏笔")
        answer = QMessageBox.question(
            self,
            "移入回收站",
            f"确定将伏笔《{title}》移入回收站吗？\n\n移入后仍可在回收站恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            ProjectDataStore(self._project).delete_foreshadowing(str(note.get("id") or ""))
        except (KeyError, OSError, ValueError) as exc:
            QMessageBox.warning(self, "删除伏笔失败", str(exc))
            return
        self.foreshadowing_changed.emit()

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
        chapter = ProjectDataStore(project).load_chapter(chapter_id)
        title_row = QHBoxLayout()
        title = QLabel(chapter.title)
        title.setObjectName("sectionTitle")
        chapter_number = chapter_number_from_id(chapter_id)
        number_text = f"第{chapter_number}章" if chapter_number is not None else chapter_id
        number = QLabel(number_text)
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

    def _foreshadowing_card(self, note: dict) -> QWidget:
        card = QFrame()
        card.setObjectName("sectionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        title_row = QHBoxLayout()
        title = QLabel(str(note.get("title") or "未命名伏笔"))
        title.setWordWrap(True)
        title.setObjectName("sectionTitle")
        status = QLabel(self._status_label(str(note.get("status") or "open")))
        status.setObjectName("savedBadge" if note.get("status") == "resolved" else "dirtyBadge")
        title_row.addWidget(title, 1)
        title_row.addWidget(status)
        layout.addLayout(title_row)
        first_seen = str(note.get("first_seen_chapter") or "未记录")
        recent = str(note.get("recent_seen_chapter") or "未记录")
        planned = str(note.get("planned_resolution_chapter") or "未设置")
        hint = QLabel(f"首次：{first_seen}  ·  最近：{recent}  ·  计划回收：{planned}")
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)
        characters = ", ".join(note.get("related_characters") or [])
        if characters:
            related = QLabel(f"关联人物：{characters}")
            related.setObjectName("mutedLabel")
            layout.addWidget(related)
        description = str(note.get("note") or "").strip()
        if description:
            body = QLabel(description)
            body.setWordWrap(True)
            body.setObjectName("mutedLabel")
            layout.addWidget(body)
        buttons = QHBoxLayout()
        edit = QPushButton("编辑")
        edit.setObjectName("secondaryButton")
        set_button_icon(edit, "edit", size=16)
        edit.clicked.connect(lambda _checked=False, item=dict(note): self._edit_foreshadowing(item))
        remove = QPushButton("移入回收站")
        remove.setObjectName("ghostButton")
        set_button_icon(remove, "delete", size=16)
        remove.clicked.connect(lambda _checked=False, item=dict(note): self._delete_foreshadowing(item))
        buttons.addStretch(1)
        buttons.addWidget(edit)
        buttons.addWidget(remove)
        layout.addLayout(buttons)
        return card

    @staticmethod
    def _foreshadowing_sort_key(note: dict) -> tuple[int, int, str]:
        status_rank = {"open": 0, "resolved": 1, "abandoned": 2}
        priority_rank = {"high": 0, "medium": 1, "low": 2}
        return (
            status_rank.get(str(note.get("status") or ""), 3),
            priority_rank.get(str(note.get("priority") or ""), 3),
            str(note.get("title") or "").casefold(),
        )

    @staticmethod
    def _status_label(status: str) -> str:
        return {"open": "未回收", "resolved": "已回收", "abandoned": "已放弃"}.get(status, status)

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
        number = chapter_number_from_id(chapter_id)
        return number if number is not None else -1

    @staticmethod
    def _context_health(state: dict, summaries: dict) -> int:
        checks = (
            bool(summaries),
            bool(state.get("current_location")),
            bool(state.get("characters")),
            bool(state.get("current_chapter")),
        )
        return round(sum(checks) / len(checks) * 100)
