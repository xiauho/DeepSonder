"""Author confirmation and editing of book expression references."""
import copy
import uuid
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QCheckBox, QComboBox, QPushButton, QDialogButtonBox, QTabWidget,
    QWidget, QScrollArea, QListWidget, QHBoxLayout)
from core.style_library import SCENES, PROFILE_FIELDS, validate
from .prose_review_dialog import _fit

class StyleLibraryDialog(QDialog):
    def __init__(self, value, chapter_id="", parent=None):
        super().__init__(parent)
        self.value = copy.deepcopy(value)
        self.chapter_id = chapter_id
        self.setWindowTitle("本书文风与样文")
        layout = QVBoxLayout(self)
        hint = QLabel("仅参考表达，不引入样文人物或情节。原有写作风格要求优先；保存后对新任务生效。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.enabled = QCheckBox("启用本书画像与样文（保存即确认）")
        self.enabled.setChecked(value["enabled"])
        layout.addWidget(self.enabled)
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        page = QWidget(); form = QFormLayout(page)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.fields = {}
        for name in PROFILE_FIELDS:
            edit = QPlainTextEdit(value["profile"].get(name, ""))
            edit.setMaximumHeight(85); edit.setAccessibleName(name)
            edit.setStyleSheet("QPlainTextEdit { border: 1px solid palette(mid); background: palette(base); padding: 4px; }")
            form.addRow(name + "（最多 600 字）", edit); self.fields[name] = edit
        draft = QPushButton("从已填写的样文特征整理候选")
        draft.clicked.connect(self._draft); form.addRow(draft)
        self.scene = QComboBox(); self.scene.addItems(SCENES)
        self.scene.setCurrentText(value["chapter_scenes"].get(chapter_id, "通用"))
        self.scene.setEnabled(bool(chapter_id))
        form.addRow("当前章节场景（未指定的章节只用通用样文）", self.scene)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        tabs.addTab(scroll, "文风画像")
        samples = QWidget(); rows = QVBoxLayout(samples)
        self.samples = QListWidget(); self.samples.setAccessibleName("本书样文")
        rows.addWidget(self.samples)
        actions = QHBoxLayout()
        for label, slot in (("添加", self._add), ("编辑", self._edit), ("删除", self._remove)):
            button = QPushButton(label); button.clicked.connect(slot); actions.addWidget(button)
        rows.addLayout(actions); tabs.addTab(samples, "样文")
        history = QPlainTextEdit(); history.setReadOnly(True)
        history.setPlainText("当前版本：" + str(value["revision"]) + "\n本书资料保留最近 20 次保存前的完整快照。\n" + "\n".join("历史版本 " + str(v.get("revision", "?")) for v in reversed(value["history"])))
        tabs.addTab(history, "版本记录")
        self.error = QLabel(); self.error.setWordWrap(True); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("确认并保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._submit); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons); self._refresh(); _fit(self)

    def _refresh(self):
        self.samples.clear()
        for s in self.value["samples"]:
            self.samples.addItem(f"{s['title']} · {s['scene']} · v{s.get('version', 1)}" + ("" if s["enabled"] else " · 已停用"))

    def _add(self):
        dialog = SampleDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.value["samples"].append(dialog.value); self._refresh()

    def _edit(self):
        index = self.samples.currentRow()
        if index < 0: return
        dialog = SampleDialog(self.value["samples"][index], self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.value["samples"][index] = dialog.value; self._refresh()

    def _remove(self):
        index = self.samples.currentRow()
        if index >= 0:
            self.value["samples"].pop(index); self._refresh()

    def _draft(self):
        # Deterministic candidate, never invent a preference or silently enable it.
        traits = list(dict.fromkeys(s["traits"] for s in self.value["samples"] if s["enabled"]))
        if self.fields["描写"].toPlainText().strip():
            self.error.setText("描写栏已有内容，请先自行整理；不会覆盖已有要求。")
            return
        self.fields["描写"].setPlainText("；".join(traits)[:600])
        self.error.setText("已整理候选，请检查后再确认保存。未自动启用。")

    def _submit(self):
        self.value["enabled"] = self.enabled.isChecked()
        self.value["profile"] = {k: v.toPlainText().strip() for k, v in self.fields.items()}
        if self.chapter_id: self.value["chapter_scenes"][self.chapter_id] = self.scene.currentText()
        try: validate(self.value)
        except ValueError as exc:
            self.error.setText(str(exc)); return
        self.accept()

class SampleDialog(QDialog):
    def __init__(self, sample=None, parent=None):
        super().__init__(parent)
        self.value = copy.deepcopy(sample) if sample else {"id": uuid.uuid4().hex, "enabled": True, "scene": "通用"}
        self.setWindowTitle("样文表达参考")
        layout = QVBoxLayout(self); body = QWidget(); form = QFormLayout(body)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.fields = {}
        for key, label in (("title", "名称"), ("source", "来源与版本（例如：本人草稿，第 3 版）"), ("traits", "希望参考的表达特征"), ("text", "样文（最多 4000 字，仅粘贴文本）")):
            edit = QPlainTextEdit() if key in ("traits", "text") else QLineEdit()
            if isinstance(edit, QPlainTextEdit):
                edit.setPlainText(self.value.get(key, "")); edit.setMinimumHeight(80)
                edit.setStyleSheet("QPlainTextEdit { border: 1px solid palette(mid); background: palette(base); padding: 4px; }")
            else: edit.setText(self.value.get(key, ""))
            edit.setAccessibleName(label); form.addRow(label, edit); self.fields[key] = edit
        self.scene = QComboBox(); self.scene.addItems(SCENES); self.scene.setCurrentText(self.value["scene"])
        form.addRow("场景标签", self.scene)
        self.enabled = QCheckBox("启用此样文"); self.enabled.setChecked(self.value["enabled"]); form.addRow(self.enabled)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(body); layout.addWidget(scroll)
        self.error = QLabel(); self.error.setWordWrap(True); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存到待确认列表")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._submit); buttons.rejected.connect(self.reject); layout.addWidget(buttons); _fit(self, 680, 700)

    def _submit(self):
        for k, edit in self.fields.items():
            self.value[k] = (edit.toPlainText() if isinstance(edit, QPlainTextEdit) else edit.text()).strip()
        self.value.update(scene=self.scene.currentText(), enabled=self.enabled.isChecked())
        from core.style_library import empty_library
        value = empty_library(); value["samples"] = [self.value]
        try: validate(value)
        except ValueError as exc:
            self.error.setText(str(exc)); return
        self.accept()
