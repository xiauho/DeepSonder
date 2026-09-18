"""A compact native form for optional author direction."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QLabel, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget
from core.story_plan import FIELDS, empty_plan, parse_plan, serialize_plan


class StoryPlanForm(QScrollArea):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self._loading = False
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(10)
        intro = QLabel("只记录全书方向，以下内容均可留空。具体事件安排在时间线中，本章细节写在剧情简写中。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.enabled = QCheckBox("用于 AI 创作")
        self.enabled.setToolTip("保存后生效。关闭时，这些内容仅作为作者草稿。")
        layout.addWidget(self.enabled)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setObjectName("mutedLabel")
        layout.addWidget(self.hint)
        self.fields = {}
        for key, label, example in FIELDS:
            field = QPlainTextEdit()
            field.setObjectName("storyPlanField")
            field.setAccessibleName(label)
            field.setPlaceholderText(example)
            field.setMinimumHeight(100)
            field.setMaximumHeight(130)
            caption = QLabel(label + "（可选）")
            caption.setBuddy(field)
            layout.addWidget(caption)
            layout.addWidget(field)
            self.fields[key] = field
            field.textChanged.connect(self._changed)
        layout.addStretch()
        self.setWidget(body)
        self.enabled.toggled.connect(self._changed)
        self.load(serialize_plan(empty_plan()))

    def load(self, content: str) -> None:
        data = parse_plan(content)
        self._loading = True
        self.enabled.setChecked(data["enabled"])
        for key, field in self.fields.items():
            field.setPlainText(data[key])
        self._loading = False
        self._update_hint()

    def content(self) -> str:
        data = empty_plan()
        data["enabled"] = self.enabled.isChecked()
        data.update({key: field.toPlainText() for key, field in self.fields.items()})
        return serialize_plan(data)

    def focus_field(self) -> None:
        self.fields["core"].setFocus()

    def _update_hint(self) -> None:
        self.hint.setText("已选择用于 AI：仅作方向参考，不会自动把未来计划视为既成事实。保存后生效。"
                          if self.enabled.isChecked() else "仅作者可用：不会加入 AI 创作上下文。保存后生效。")

    def _changed(self) -> None:
        if not self._loading:
            self._update_hint()
            self.changed.emit()
