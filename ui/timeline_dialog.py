"""Timeline event editing and chapter writing-material selection."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLineEdit, QPlainTextEdit, QComboBox, QCheckBox, QListWidget,
    QListWidgetItem, QPushButton, QLabel, QScrollArea, QWidget, QMessageBox)
from core.timeline_events import TimelineStore, STATES, USES


def combo(values, blank=None):
    widget = QComboBox()
    if blank:
        widget.addItem(blank, "")
    for key, label in values.items():
        widget.addItem(label, key)
    return widget


def buttons(dialog, layout, ok="保存", cancel="取消"):
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Ok).setText(ok)
    box.button(QDialogButtonBox.StandardButton.Cancel).setText(cancel)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)
    return box


class EventEditorDialog(QDialog):
    def __init__(self, store, event=None, parent=None):
        super().__init__(parent)
        self.store, self.event_data = store, event
        self.saved_id = None
        self.setWindowTitle("编辑事件" if event else "新建事件")
        self.resize(580, 660)
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        values = event or {}
        self.fields = {}
        for key, label, limit in (("title", "事件标题（必填）", 120), ("time", "故事时间（可留空或填写“三天后”）", 200),
                ("characters", "参与人物", 500), ("location", "地点", 200), ("storyline", "所属故事线", 200)):
            field = QLineEdit(values.get(key, ""))
            field.setMaxLength(limit)
            self.fields[key] = field
            form.addRow(label, field)
        self.description = QPlainTextEdit(values.get("description", ""))
        self.description.setMinimumHeight(100)
        form.addRow("事件描述（原因、经过、结果，最多 4000 字）", self.description)
        self.state = combo(STATES)
        self.state.setCurrentIndex(max(0, self.state.findData(values.get("state", "planned"))))
        form.addRow("剧情状态（由作者确认，不随 AI 生成自动改变）", self.state)
        self.major = QCheckBox("标记为大事件")
        self.major.setChecked(values.get("major", False))
        form.addRow(self.major)
        self.chapters = QListWidget()
        self.chapters.setMinimumHeight(115)
        for path in store.project.list_chapters():
            chapter = store.project.load_chapter(path.stem)
            item = QListWidgetItem(f"{path.stem} · {chapter.title}")
            item.setData(Qt.ItemDataRole.UserRole, path.stem)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if path.stem in values.get("chapter_ids", []) else Qt.CheckState.Unchecked)
            self.chapters.addItem(item)
        form.addRow("关联章节（计划写入／已经出现的章节）", self.chapters)
        scroll.setWidget(body)
        root.addWidget(scroll)
        hint = QLabel("保存修改后，该事件需在下次写作时重新选择用途。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.error = QLabel()
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        buttons(self, root)

    def accept(self):
        values = {key: field.text() for key, field in self.fields.items()}
        values.update(description=self.description.toPlainText(), state=self.state.currentData(), major=self.major.isChecked(),
            chapter_ids=[self.chapters.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.chapters.count())
                         if self.chapters.item(i).checkState() == Qt.CheckState.Checked])
        try:
            self.saved_id = self.store.save_event(values, self.event_data["id"] if self.event_data else None)
        except (OSError, ValueError) as exc:
            self.error.setText(str(exc))
            return
        super().accept()


class EventMaterialDialog(QDialog):
    def __init__(self, project, chapter_id, parent=None):
        super().__init__(parent)
        self.store, self.chapter_id = TimelineStore(project), chapter_id
        self.setWindowTitle("选择本次事件素材")
        self.resize(650, 560)
        root = QVBoxLayout(self)
        hint = QLabel("最多选择 8 个事件。背景事实须已出现在当前或前文章节；本次展开用于推进计划或丰富细节；暂不揭示用于约束后续发展。选择会记住到本章，下次运行可修改。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索事件")
        self.search.setAccessibleName("搜索本次事件素材")
        root.addWidget(self.search)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        content = QVBoxLayout(body)
        self.rows = []
        selected = self.store.selection(chapter_id)
        for event in self.store.events():
            if event['state'] == 'abandoned':
                continue
            panel = QWidget()
            row = QVBoxLayout(panel)
            title = QLabel(f"{event['time'] or '时间待定'} · {event['title']} · {STATES[event['state']]}")
            title.setWordWrap(True)
            row.addWidget(title)
            reason = "关联本章" if chapter_id in event['chapter_ids'] else "作者手动选择"
            description = QLabel(f"{reason} · {event['description'][:160] or '暂无描述'}")
            description.setWordWrap(True)
            row.addWidget(description)
            use = combo(USES, "不使用")
            use.setAccessibleName(f"{event['title']}的素材用途")
            use.setCurrentIndex(max(0, use.findData(selected.get(event['id'], ''))))
            for index in range(1, use.count()):
                try:
                    self.store.render(chapter_id, {event['id']: use.itemData(index)})
                except ValueError as exc:
                    use.model().item(index).setEnabled(False)
                    use.setItemData(index, str(exc), Qt.ItemDataRole.ToolTipRole)
            use.currentIndexChanged.connect(self.update_summary)
            row.addWidget(use)
            content.addWidget(panel)
            self.rows.append((event, use, panel))
        content.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        clear = QPushButton("清空选择")
        clear.clicked.connect(lambda: [use.setCurrentIndex(0) for _, use, _ in self.rows])
        root.addWidget(clear)
        self.error = QLabel()
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setAccessibleName("已选事件摘要")
        self.summary.setMaximumHeight(90)
        root.addWidget(self.summary)
        buttons(self, root, "使用所选素材", "取消写作")
        self.update_summary()
        self.search.textChanged.connect(self.filter)

    def update_summary(self, *_):
        if not hasattr(self, "summary"):
            return
        selected = [f"{event['title']}（{USES[use.currentData()]}）" for event, use, _ in self.rows if use.currentData()]
        self.summary.setPlainText(f"已选 {len(selected)} / 8" + ("：" + "、".join(selected) if selected else " · 本次不使用事件素材"))

    def filter(self, query):
        for event, _, panel in self.rows:
            panel.setVisible(query.casefold() in " ".join(str(v) for v in event.values()).casefold())

    def accept(self):
        try:
            self.store.save_selection(self.chapter_id, {event['id']: use.currentData() for event, use, _ in self.rows if use.currentData()})
        except (OSError, ValueError) as exc:
            self.error.setText(str(exc))
            return
        super().accept()
