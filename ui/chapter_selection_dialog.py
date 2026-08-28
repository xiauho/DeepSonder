"""Chapter picker used before running a report-page consistency check."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)


class ChapterSelectionDialog(QDialog):
    """Require an explicit chapter choice without changing the editor route."""

    def __init__(
        self,
        chapters: list[tuple[str, str, Path]],
        *,
        default_chapter_id: str | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择检查章节")
        self.resize(520, 440)
        self._list = QListWidget()
        self._list.setObjectName("chapterSelectionList")
        self._list.itemDoubleClicked.connect(lambda _item: self.accept())

        layout = QVBoxLayout(self)
        intro = QLabel("请选择要检查的章节。检查将在当前报告页执行，不会自动切换写作台。")
        intro.setWordWrap(True)
        intro.setObjectName("mutedLabel")
        layout.addWidget(intro)
        layout.addWidget(self._list, 1)

        for chapter_id, title, path in chapters:
            item = QListWidgetItem(f"{title or chapter_id}  ·  {chapter_id}")
            item.setData(Qt.ItemDataRole.UserRole, chapter_id)
            item.setToolTip(str(path))
            self._list.addItem(item)

        selected_index = 0
        if default_chapter_id:
            for index in range(self._list.count()):
                if self._list.item(index).data(Qt.ItemDataRole.UserRole) == default_chapter_id:
                    selected_index = index
                    break
        if self._list.count():
            self._list.setCurrentRow(selected_index)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("检查所选章节")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_chapter_id(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value).strip() if value else None
