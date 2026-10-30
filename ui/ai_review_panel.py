"""Embed existing review controls while retaining their guarded decision lifecycle."""
from __future__ import annotations

from difflib import unified_diff

from PySide6.QtCore import QEvent, QEventLoop, QTimer, Qt, Signal
from PySide6.QtWidgets import (QBoxLayout, QDialog, QDialogButtonBox, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from ui.elided_label import ElidedLabel


def review_diff(original: str, proposed: str) -> str:
    """Bound a display-only diff; never truncate a proposal used for writing."""
    if original == proposed:
        return "当前选择未改变正文。"
    if len(original) + len(proposed) > 200_000:
        return "内容较长，未生成逐行差异。请在审阅页查看完整原文与候选内容。"
    result, count = [], 0
    for line in unified_diff(original.splitlines(), proposed.splitlines(),
                             fromfile="原文", tofile="当前选择", lineterm=""):
        count += len(line) + 1
        if count > 60_000:
            result.append("\n差异展示已达到上限；完整内容仍保留在审阅页，写入不会使用截断的差异。")
            break
        result.append(line)
    return "\n".join(result)


class AIReviewPanel(QFrame):
    visibility_requested = Signal(bool)
    postponed = Signal()
    resumed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("aiReviewPanel")
        self.active = False
        self.discarded = False
        self.stale = False
        self.dialog = None
        self._retired = []
        self._context_matches = None
        self._diff_provider = None
        self._footer_record = None
        self._message_content = self._message_controls = None
        self._loop = None
        self._source_timer = QTimer(self)
        self._source_timer.setInterval(1000)
        self._source_timer.timeout.connect(self.check_sources)
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        heading = QHBoxLayout()
        self.title = QLabel("AI 结果审阅")
        self.title.setObjectName("panelTitle")
        self.title.setWordWrap(True)
        heading.addWidget(self.title, 1)
        self.back_button = QPushButton("稍后审阅")
        self.back_button.setToolTip("保留候选、勾选和当前审阅位置，返回工作区")
        self.back_button.clicked.connect(self.postpone)
        heading.addWidget(self.back_button)
        root.addLayout(heading)
        self.target = ElidedLabel()
        self.target.setTextFormat(Qt.TextFormat.PlainText)
        self.target.setMinimumWidth(0)
        self.target.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        root.addWidget(self.target)
        self.source_status = QLabel()
        self.source_status.setWordWrap(True)
        root.addWidget(self.source_status)
        self.effect = QLabel("确认前不会修改正文或项目数据。")
        self.effect.setWordWrap(True)
        self.effect.setObjectName("mutedLabel")
        root.addWidget(self.effect)
        self.tabs = QTabWidget()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs.addTab(self.scroll, "审阅")
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setAccessibleName("当前所选内容的逐行差异")
        self.tabs.addTab(self.diff, "差异")
        self.tabs.currentChanged.connect(self._refresh_diff)
        root.addWidget(self.tabs, 1)
        self.footer = QWidget()
        self._footer_layout = QVBoxLayout(self.footer)
        self._footer_layout.setContentsMargins(0, 0, 0, 0)
        self.footer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        root.addWidget(self.footer)
        self.discard_button = QPushButton("放弃本次结果")
        self.discard_button.setObjectName("ghostButton")
        self.discard_button.clicked.connect(self.discard)
        self.discard_button.hide()
        root.addWidget(self.discard_button, alignment=Qt.AlignmentFlag.AlignLeft)

    def begin(self, label: str, chapter: str):
        self.active = True
        self.discarded = self.stale = False
        self.title.setText("AI 结果审阅 · " + label)
        self.target.setText("目标章节：" + chapter)
        self.target.setToolTip(self.target.text())

    def context_current(self):
        return not self.active or (not self.stale and not self.discarded)

    def postpone(self):
        if self.active:
            self._source_timer.stop()
            self.visibility_requested.emit(False)
            self.postponed.emit()

    def resume(self):
        if self.active:
            self.check_sources()
            if not self.stale:
                self._source_timer.start()
            self.visibility_requested.emit(True)
            self.resumed.emit()

    def discard(self):
        self.discarded = True
        if self.dialog is not None:
            self.dialog.reject()

    def check_sources(self):
        if self.dialog is None:
            return
        current = not self.stale
        if current:
            try:
                current = bool(self._context_matches()) if self._context_matches else True
            except Exception:
                current = False
        self.stale = self.stale or not current
        text = ("来源已变化，写入已阻止；请放弃结果后重新生成。" if self.stale
                else "来源版本一致 · 确认前尚未写入")
        if self.source_status.text() != text:
            self.source_status.setText(text)
            self.source_status.setObjectName("saveErrorMessage" if self.stale else "canonSavedBadge")
            self.source_status.style().unpolish(self.source_status)
            self.source_status.style().polish(self.source_status)
        if self.stale:
            self._source_timer.stop()
            for button in self.dialog.findChildren(QPushButton) + self.footer.findChildren(QPushButton):
                if button.property("reviewNeedsContext"):
                    button.setEnabled(False)

    def eventFilter(self, watched, event):
        if (watched is self.dialog or watched is self._message_content) and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.postpone()
            return True
        if watched.property("reviewNeedsContext"):
            if event.type() == QEvent.Type.EnabledChange and self.stale and watched.isEnabled():
                watched.setEnabled(False)
            if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress):
                self.check_sources()
                if self.stale:
                    return True
        return super().eventFilter(watched, event)

    def _refresh_diff(self, *_args):
        if self._diff_provider is not None and self.tabs.currentIndex() == 1:
            try:
                self.diff.setPlainText(review_diff(*self._diff_provider()))
            except (ValueError, RuntimeError):
                self.diff.setPlainText("当前选择暂时无法生成差异，请回到审阅页核对。")

    def _mount_footer(self, dialog):
        layout = dialog.layout()
        box = dialog.findChild(QDialogButtonBox)
        index = layout.indexOf(box) if box is not None else layout.count() - 1
        if index < 0:
            return
        grid_position = layout.getItemPosition(index) if isinstance(layout, QGridLayout) else None
        item = layout.takeAt(index)
        control = item.widget() or item.layout()
        if control is None:
            layout.addItem(item)
            return
        direction = control.direction() if isinstance(control, QBoxLayout) else None
        control.setParent(None)
        if item.widget() is not None:
            self._footer_layout.addWidget(control)
        else:
            self._footer_layout.addLayout(control)
        self._footer_record = (layout, index, control, grid_position, direction)
        self._fit_footer()

    def _fit_footer(self):
        if self._footer_record is None:
            return
        control = self._footer_record[2]
        if isinstance(control, QDialogButtonBox):
            needed = sum(b.sizeHint().width() + 8 for b in control.buttons())
            control.setOrientation(Qt.Orientation.Vertical if needed > self.width() - 28 else Qt.Orientation.Horizontal)
        elif isinstance(control, QBoxLayout):
            needed = sum(control.itemAt(i).widget().sizeHint().width() + 8
                         for i in range(control.count()) if control.itemAt(i).widget() is not None)
            control.setDirection(QBoxLayout.Direction.TopToBottom if needed > self.width() - 28
                                 else QBoxLayout.Direction.LeftToRight)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_footer()

    def _unmount_footer(self):
        if self._message_controls is not None:
            self._footer_layout.removeWidget(self._message_controls)
            self._message_controls.hide()
            self._message_controls.deleteLater()
            self._message_content.deleteLater()
            self._message_controls = self._message_content = None
            return
        if self._footer_record is None:
            return
        layout, index, control, grid, direction = self._footer_record
        if isinstance(control, QWidget):
            self._footer_layout.removeWidget(control)
            control.setParent(self.dialog)
            if grid is not None:
                layout.addWidget(control, *grid)
            else:
                layout.insertWidget(index, control)
        else:
            self._footer_layout.removeItem(control)
            control.setParent(None)
            control.setDirection(direction)
            layout.insertLayout(index, control)
        self._footer_record = None

    def _message_view(self, dialog):
        """QMessageBox rebuilds its private button layout on show; mirror its explicit actions."""
        content = QWidget()
        layout = QVBoxLayout(content)
        for text in (dialog.text(), dialog.informativeText()):
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            layout.addWidget(label)
        evidence = QPlainTextEdit(dialog.detailedText())
        evidence.setReadOnly(True)
        evidence.setAccessibleName("阻断原因、事实证据和正文锚点")
        evidence.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(evidence, 1)
        controls = QWidget()
        buttons = QVBoxLayout(controls)
        buttons.setContentsMargins(0, 0, 0, 0)
        for original in dialog.review_actions:
            button = QPushButton("放弃结果" if original.property("reviewDiscard") else original.text())
            button.setAutoDefault(False)
            button.setProperty("reviewNeedsContext", bool(original.property("reviewNeedsContext")))
            button.setProperty("reviewDiscard", bool(original.property("reviewDiscard")))
            button.clicked.connect(original.click)
            buttons.addWidget(button)
        self._footer_layout.addWidget(controls)
        self._message_content, self._message_controls = content, controls
        content.installEventFilter(self)
        return content

    def present(self, dialog, *, context_matches=None, diff_provider=None, effect=""):
        if not self.active:
            return dialog.exec()
        self.dialog = dialog
        self._context_matches = context_matches
        self._diff_provider = diff_provider
        self.tabs.setCurrentIndex(0)
        self.tabs.setTabVisible(1, diff_provider is not None)
        self.effect.setText(effect or "确认前不会修改正文或项目数据。")
        original_parent, flags = dialog.parentWidget(), dialog.windowFlags()
        message = isinstance(dialog, QMessageBox) and hasattr(dialog, "review_actions")
        if message:
            content = self._message_view(dialog)
        else:
            dialog.setParent(self.scroll)
            dialog.setWindowFlags(Qt.WindowType.Widget)
            dialog.setWindowModality(Qt.WindowModality.NonModal)
            dialog.setMinimumSize(0, 0)
            self._mount_footer(dialog)
            content = dialog
        self.scroll.setWidget(content)
        self.discard_button.setVisible(not any(b.property("reviewDiscard")
            for b in self.footer.findChildren(QPushButton)))
        dialog.installEventFilter(self)
        for button in dialog.findChildren(QPushButton) + self.footer.findChildren(QPushButton):
            if button.property("reviewNeedsContext"):
                button.installEventFilter(self)
        loop = QEventLoop()
        self._loop = loop
        dialog.finished.connect(loop.quit)
        dialog.destroyed.connect(loop.quit)
        self.visibility_requested.emit(True)
        content.show()
        self.check_sources()
        if not self.stale:
            self._source_timer.start()
        try:
            # Keep the existing synchronous commit guards and retained snapshot
            # alive while the modeless workspace remains usable. Postponing
            # only hides this view; accept/reject is the actual decision.
            loop.exec()
            self.check_sources()
            result = dialog.result()
            if result == QDialog.DialogCode.Accepted and (self.stale or self.discarded):
                if hasattr(dialog, "confirmed"):
                    dialog.confirmed = False
                result = QDialog.DialogCode.Rejected
            if not isinstance(dialog, QMessageBox) and result == QDialog.DialogCode.Rejected:
                self.discarded = True
            return result
        finally:
            self._source_timer.stop()
            dialog.finished.disconnect(loop.quit)
            dialog.destroyed.disconnect(loop.quit)
            self._unmount_footer()
            self.scroll.takeWidget()
            dialog.hide()
            if not message:
                dialog.setParent(original_parent)
                dialog.setWindowFlags(flags)
            self._retired.append(dialog)
            self.dialog = self._loop = None
            self.visibility_requested.emit(False)

    def finish(self):
        self._source_timer.stop()
        self.active = False
        self.visibility_requested.emit(False)
        for dialog in self._retired:
            dialog.deleteLater()
        self._retired.clear()
        self._context_matches = self._diff_provider = None


class ReviewWorkspace(QSplitter):
    """Keep both panes readable; use a full review page at narrow logical widths."""
    def __init__(self, workspace, review, parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.workspace, self.review = workspace, review
        self._review_visible = False
        self._mode = None
        self.setChildrenCollapsible(False)
        self.setHandleWidth(5)
        self.addWidget(workspace)
        self.addWidget(review)
        review.hide()
        review.visibility_requested.connect(self.show_review)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._reflow)

    def show_review(self, visible):
        self._review_visible = visible
        self._mode = None
        self._reflow()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_timer.start(0)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(self, "_layout_timer"):
            self._layout_timer.start(0)

    def _reflow(self):
        preferred = max(440, min(560, self.fontMetrics().horizontalAdvance("中") * 28))
        side = self.width() >= max(920, self.workspace.minimumSizeHint().width()) + preferred + self.handleWidth()
        mode = (self._review_visible, side, preferred)
        if mode == self._mode:
            return
        self._mode = mode
        self.workspace.setVisible(not self._review_visible or side)
        self.review.setVisible(self._review_visible)
        self.review.back_button.setText("稍后审阅" if side else "返回正文")
        if self._review_visible:
            self.setSizes([max(0, self.width() - preferred), preferred] if side else [0, self.width()])
        else:
            self.setSizes([self.width(), 0])
