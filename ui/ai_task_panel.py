"""One session-local surface for AI execution, review entry and diagnostics."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout,
)

TASK_LABELS = {
    "selection_expand": "选区扩写", "style_polish": "选区文风润色", "style_review": "文风审校",
    "expand": "按章纲生成正文", "continuation": "章节续写", "check": "一致性检查",
    "memory": "故事记忆", "repair": "一致性修复", "writing_supplement": "差额补写",
}


class AITaskPanel(QFrame):
    review_requested = Signal()
    discard_requested = Signal()
    cancel_requested = Signal()
    close_requested = Signal()
    copy_requested = Signal()
    clear_requested = Signal()
    context_requested = Signal()
    report_requested = Signal()
    state_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("outputContainer")
        self.state = "idle"
        self.task_id = None
        self.task_kind = ""
        self.report_ready = False
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(6)
        header = QHBoxLayout()
        self.title = QLabel("AI 任务")
        self.title.setObjectName("panelTitle")
        self.scope = QLabel()
        self.scope.setTextFormat(Qt.TextFormat.PlainText)
        self.scope.setObjectName("mutedLabel")
        self.scope.setMinimumWidth(0)
        self.scope.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(self.title)
        header.addWidget(self.scope, 1)
        close = QPushButton("收起")
        close.setObjectName("ghostButton")
        close.clicked.connect(self.close_requested)
        header.addWidget(close)
        root.addLayout(header)
        self.detail = QLabel("选择 AI 创作菜单中的任务。生成内容需审阅确认后才会写入。")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        root.addWidget(self.detail)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(6)
        root.addWidget(self.progress)
        actions = QHBoxLayout()
        self.review_button = QPushButton("审阅结果")
        self.review_button.setObjectName("accentButton")
        self.review_button.clicked.connect(self.review_requested)
        self.discard_button = QPushButton("放弃结果")
        self.discard_button.clicked.connect(self.discard_requested)
        self.cancel_button = QPushButton("取消任务")
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.context_button = QPushButton("本次上下文")
        self.context_button.clicked.connect(self.context_requested)
        self.report_button = QPushButton("检查报告")
        self.report_button.clicked.connect(self.report_requested)
        for button in (self.review_button, self.discard_button, self.cancel_button,
                       self.context_button, self.report_button):
            actions.addWidget(button)
        actions.addStretch(1)
        self.log_toggle = QToolButton()
        self.log_toggle.setText("展开工作记录")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        actions.addWidget(self.log_toggle)
        root.addLayout(actions)
        self.log_container = QFrame()
        log_layout = QVBoxLayout(self.log_container)
        log_layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        controls.addStretch(1)
        for text, signal in (("复制记录", self.copy_requested), ("清空记录", self.clear_requested)):
            button = QPushButton(text)
            button.setObjectName("ghostButton")
            button.clicked.connect(signal)
            controls.addWidget(button)
        log_layout.addLayout(controls)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setObjectName("outputPanel")
        self.output.setAccessibleName("AI 工作记录")
        self.output.setMinimumHeight(80)
        log_layout.addWidget(self.output, 1)
        root.addWidget(self.log_container, 1)
        self.log_container.hide()
        self._refresh()

    def _toggle_log(self, visible: bool) -> None:
        self.log_container.setVisible(visible)
        self.log_toggle.setText("收起工作记录" if visible else "展开工作记录")

    def _refresh(self) -> None:
        pending = self.state == "pending"
        running = self.state in {"running", "cancelling"}
        self.review_button.setVisible(pending)
        self.discard_button.setVisible(pending)
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(self.state == "running")
        self.progress.setVisible(running)
        self.context_button.setVisible(self.task_id is not None)
        self.report_button.setVisible(self.report_ready and self.state == "reviewed")
        self.title.setText("AI 任务 · " + {
            "idle": "就绪", "running": "处理中", "cancelling": "正在取消",
            "pending": "待审阅", "reviewing": "审阅中", "reviewed": "审阅结束",
            "finished": "后台已结束", "failed": "失败", "cancelled": "已取消",
            "discarded": "已放弃",
        }[self.state])
        self.state_changed.emit()

    def start(self, token) -> None:
        self.task_id = token.task_id
        self.task_kind = token.kind
        self.report_ready = False
        self.state = "running"
        self.scope.setText(f"{TASK_LABELS.get(token.kind, 'AI')} · {token.chapter_id}")
        self.scope.setToolTip(self.scope.text())
        self.detail.setText("正在后台处理，可继续写作或收起面板。")
        self.log_toggle.setChecked(False)
        self._refresh()

    def pending(self, token) -> None:
        self.task_id, self.task_kind = token.task_id, token.kind
        self.scope.setText(f"{TASK_LABELS.get(token.kind, 'AI')} · {token.chapter_id}")
        self.scope.setToolTip(self.scope.text())
        self.state = "pending"
        self.detail.setText("结果已就绪，尚未写入。点击审阅结果查看预览；若正文或资料已变化，将重新检查是否仍可采用。")
        self._refresh()

    def set_state(self, state: str, detail: str | None = None) -> None:
        self.state = state
        if detail is not None:
            self.detail.setText(detail)
        self._refresh()

    def finish(self, token) -> None:
        if token.task_id == self.task_id and self.state in {"running", "cancelling"}:
            self.set_state("finished", "后台任务已结束，请查看工作记录。")

    def reset(self) -> None:
        self.task_id = None
        self.task_kind = ""
        self.report_ready = False
        self.scope.clear()
        self.log_toggle.setChecked(False)
        self.set_state("idle", "选择 AI 创作菜单中的任务。生成内容需审阅确认后才会写入。")
