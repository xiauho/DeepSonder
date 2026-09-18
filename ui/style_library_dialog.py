"""Author confirmation and editing of book expression references."""
import copy
import uuid
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QCheckBox, QComboBox, QPushButton, QDialogButtonBox, QTabWidget,
    QWidget, QScrollArea, QListWidget, QHBoxLayout)
from core.style_library import SCENES, PROFILE_FIELDS, PROFILE_LABELS, STYLE_BUDGET, validate, render_library_value
from .prose_review_dialog import _fit

class StyleLibraryDialog(QDialog):
    def __init__(self, value, chapter_id="", parent=None, *, quotes=()):
        super().__init__(parent)
        self.value = copy.deepcopy(value)
        self.chapter_id = chapter_id
        self.setWindowTitle("本书文风")
        layout = QVBoxLayout(self)
        hint = QLabel("分区域填写本书长期文风要求，留空项不添加约束。仅调整表达，不改变故事事实；确认保存后对新任务生效。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.enabled = QCheckBox("启用参考样文（不影响文风要求）")
        self.enabled.setChecked(value["enabled"])

        tabs = self.tabs = QTabWidget()
        tabs.setUsesScrollButtons(True)
        layout.addWidget(tabs, 1)
        page = QWidget(); form = QFormLayout(page)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.fields = {}
        examples = {"总体气质": "例如：冷峻克制，保留温暖的日常细节。", "叙事距离": "例如：第三人称限知，贴近主角感知。",
                    "对话": "例如：对白简短，通过停顿和潜台词传递情绪。", "描写": "例如：侧重动作与感官细节，减少抽象总结。",
                    "节奏": "例如：动作场景短句为主，日常场景适度放缓。", "避免事项": "例如：避免连续排比和总结式升华。",
                    "其他要求": "可选：填写前面各区域未覆盖的表达要求。"}
        for name in PROFILE_FIELDS:
            edit = QPlainTextEdit(value["profile"].get(name, ""))
            edit.setMinimumHeight(76); edit.setMaximumHeight(110)
            edit.setAccessibleName(PROFILE_LABELS[name]); edit.setPlaceholderText(examples[name])
            edit.setStyleSheet("QPlainTextEdit { border: 1px solid palette(mid); background: palette(base); padding: 4px; }")
            form.addRow(PROFILE_LABELS[name] + "（最多 600 字）", edit); self.fields[name] = edit
        self.scene = QComboBox(); self.scene.addItems(SCENES)
        self.scene.setAccessibleName("当前章节样文场景")
        self.scene.setCurrentText(value["chapter_scenes"].get(chapter_id, "通用"))
        self.scene.setEnabled(bool(chapter_id))

        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(page)
        tabs.addTab(scroll, "文风要求")
        samples = QWidget(); rows = QVBoxLayout(samples)
        rows.addWidget(self.enabled)
        scene_hint = QLabel("当前章节场景" if chapter_id else "未打开章节：仅预览通用样文")
        scene_hint.setWordWrap(True); rows.addWidget(scene_hint); rows.addWidget(self.scene)
        self.usage = QLabel(); self.usage.setWordWrap(True); rows.addWidget(self.usage)
        self.samples = QListWidget(); self.samples.setAccessibleName("本书样文")
        rows.addWidget(self.samples)
        actions = QHBoxLayout()
        for label, slot in (("添加", self._add), ("编辑", self._edit), ("删除", self._remove)):
            button = QPushButton(label); button.clicked.connect(slot); actions.addWidget(button)
        rows.addLayout(actions); tabs.addTab(samples, "参考样文")
        exceptions = QWidget(); exceptions_layout = QVBoxLayout(exceptions)
        exceptions_hint = QLabel("这些原句仅在文风审校与润色中受保护，不影响选区扩写。移除后需确认保存。")
        exceptions_hint.setWordWrap(True); exceptions_layout.addWidget(exceptions_hint)
        self.exceptions = QListWidget(); self.exceptions.setAccessibleName("审校例外")
        self.exceptions.addItems(quotes); exceptions_layout.addWidget(self.exceptions, 1)
        remove_exception = QPushButton("移除选中的例外")
        remove_exception.clicked.connect(self._remove_exception); exceptions_layout.addWidget(remove_exception)
        tabs.addTab(exceptions, "审校例外")
        self.error = QLabel(); self.error.setWordWrap(True); layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("确认并保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._submit); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.enabled.toggled.connect(self._update_usage)
        self.scene.currentTextChanged.connect(self._update_usage)
        for edit in self.fields.values(): edit.textChanged.connect(self._update_usage)
        self._refresh(); _fit(self)

    def quotes(self):
        return [self.exceptions.item(i).text() for i in range(self.exceptions.count())]

    def _remove_exception(self):
        if self.exceptions.currentRow() >= 0:
            self.exceptions.takeItem(self.exceptions.currentRow())

    def _update_usage(self):
        value = copy.deepcopy(self.value)
        value["enabled"] = self.enabled.isChecked()
        value["profile"] = {k: edit.toPlainText().strip() for k, edit in self.fields.items()}
        if self.chapter_id: value["chapter_scenes"][self.chapter_id] = self.scene.currentText()
        authored = any(value["profile"].values())
        rendered = render_library_value(value, self.chapter_id, budget=STYLE_BUDGET)
        scene = value["chapter_scenes"].get(self.chapter_id, "通用")
        included = 0
        for index, sample in enumerate(value["samples"]):
            used = f"<STYLE_SAMPLE id={sample['id']} " in rendered
            included += int(used)
            state = "预计纳入" if used else "已停用" if not value["enabled"] or not sample["enabled"] else "场景不匹配" if sample["scene"] not in (scene, "通用") else "预算或数量上限省略"
            self.samples.item(index).setText(f"{sample['title']} · {sample['scene']} · {state}")
        self.usage.setText(f"文风要求：{'已填写' if authored else '未填写'} · 参考样文：{'启用' if value['enabled'] else '停用'}\n预计引用 {included} 条样文；实际以任务诊断为准。")

    def _refresh(self):
        self.samples.clear()
        for s in self.value["samples"]:
            self.samples.addItem(f"{s['title']} · {s['scene']} · v{s.get('version', 1)}" + ("" if s["enabled"] else " · 已停用"))

        self._update_usage()

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

    def _submit(self):
        self.value["enabled"] = self.enabled.isChecked()
        self.value["profile"] = {k: v.toPlainText().strip() for k, v in self.fields.items() if v.toPlainText().strip() or k in self.value["profile"]}
        if self.chapter_id and (self.chapter_id in self.value["chapter_scenes"] or self.scene.currentText() != "通用"):
            self.value["chapter_scenes"][self.chapter_id] = self.scene.currentText()
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
