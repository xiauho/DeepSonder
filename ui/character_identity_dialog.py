"""Reviewable identity association, with explicit field-conflict choices."""
from __future__ import annotations
import json
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)
from core.character_identity import (conflict_options, fingerprint, load_registry,
    remove_identity, save_identity)

LABELS = {"state": "状态", "location": "位置", "power_level": "战力", "items": "物品", "relations": "关系"}


class CharacterIdentityDialog(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.registry = load_registry(project)
        self.state = project.load_story_state()
        self.choices = {}
        self.preview_key = None
        self.setWindowTitle("人物身份管理")
        screen = self.screen().availableGeometry()
        self.resize(min(600, screen.width() - 32), min(620, screen.height() - 32))
        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        hint = QLabel("将同一人物的化名、真名关联起来。原始记忆按原称呼保留；关联可撤销。较早章节不使用后续揭露的身份。")
        hint.setWordWrap(True)
        body.addWidget(hint)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.existing = QComboBox()
        self.existing.addItem("新建身份关联", None)
        for group in self.registry["groups"]:
            self.existing.addItem(" / ".join(group["names"]), group)
        form.addRow("已有关联", self.existing)
        self.primary = QLineEdit()
        self.primary.setMaxLength(80)
        form.addRow("主要显示名", self.primary)
        self.names = QPlainTextEdit()
        self.names.setPlaceholderText("每行一个名称，包含主要显示名；扩展时保留全部已有名称")
        self.names.setMaximumHeight(105)
        form.addRow("全部身份名称", self.names)
        self.chapter = QComboBox()
        for path in project.list_chapters():
            self.chapter.addItem(project.load_chapter(path.stem).title, path.stem)
        form.addRow("从此章揭露身份", self.chapter)
        self.evidence = QPlainTextEdit()
        self.evidence.setPlaceholderText("填写原文依据或作者确认说明。不会由 AI 自动判断同一人物。")
        self.evidence.setMaximumHeight(85)
        form.addRow("关联依据", self.evidence)
        body.addLayout(form)
        self.preview_button = QPushButton("预览关联与字段冲突")
        self.preview_button.clicked.connect(self.preview)
        body.addWidget(self.preview_button)
        self.conflicts_widget = QWidget()
        self.conflicts_layout = QFormLayout(self.conflicts_widget)
        self.conflicts_layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        body.addWidget(self.conflicts_widget)
        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        body.addWidget(self.feedback)
        self.remove_button = QPushButton("撤销所选身份关联…")
        self.remove_button.setEnabled(False)
        self.remove_button.clicked.connect(self.remove)
        body.addWidget(self.remove_button)
        body.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        self.save_button = buttons.addButton("确认关联并保存", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.existing.currentIndexChanged.connect(self.select_existing)
        self.primary.textChanged.connect(self.invalidate)
        self.names.textChanged.connect(self.invalidate)
        self.chapter.currentIndexChanged.connect(self.invalidate)
        self.evidence.textChanged.connect(self.invalidate)

    def values(self):
        return dict(primary=self.primary.text().strip(),
                    names=list(dict.fromkeys([self.primary.text().strip(), *self.names.toPlainText().splitlines()])),
                    chapter_id=self.chapter.currentData(), evidence=self.evidence.toPlainText().strip())

    def invalidate(self, *_):
        self.save_button.setEnabled(False)
        self.preview_key = None

    def select_existing(self):
        group = self.existing.currentData()
        self.remove_button.setEnabled(group is not None)
        self.primary.setText(group["primary"] if group else "")
        self.names.setPlainText("\n".join(group["names"]) if group else "")
        self.evidence.setPlainText(group.get("evidence", "") if group else "")
        if group:
            self.chapter.setCurrentIndex(self.chapter.findData(group["chapter_id"]))
        self.invalidate()

    def preview(self):
        while self.conflicts_layout.rowCount():
            self.conflicts_layout.removeRow(0)
        self.choices = {}
        values = self.values()
        names = [n.strip() for n in values["names"] if n.strip()]
        if len(set(names)) < 2 or not values["primary"] or not values["evidence"]:
            self.feedback.setText("请填写主要显示名、至少两个不同名称及关联依据，然后重新预览。")
            self.invalidate()
            return
        for field, options in conflict_options(self.state, names).items():
            combo = QComboBox()
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(16)
            combo.addItem("请选择采用哪条记录", None)
            for name, value in options.items():
                text = f"{name}：{json.dumps(value, ensure_ascii=False)}"
                combo.addItem(text, name)
                combo.setItemData(combo.count() - 1, text, 3)
            self.choices[field] = combo
            self.conflicts_layout.addRow(LABELS.get(field, field), combo)
        self.feedback.setText(f"确认后统一显示为“{values['primary']}”，从所选章节起关联 {len(names)} 个名称。\n"
                              "冲突字段须逐项选择；以后有证据的新状态会接续更新。正文和原始记忆不会被改写。")
        self.preview_key = fingerprint(values)
        self.save_button.setEnabled(True)

    def save(self):
        if self.preview_key != fingerprint(self.values()):
            self.feedback.setText("内容已变化，请重新预览。")
            return
        try:
            save_identity(self.project, **self.values(),
                choices={f: c.currentData() for f, c in self.choices.items()},
                expected_revision=fingerprint(self.registry), expected_state=fingerprint(self.state))
        except (ValueError, OSError) as exc:
            self.feedback.setText(str(exc))
            return
        self.accept()

    def remove(self):
        group = self.existing.currentData()
        if not group:
            return
        if QMessageBox.question(self, "撤销身份关联", "撤销后按各自称呼展示原始记忆，已保存的角色卡文字不会自动回退。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            remove_identity(self.project, group["id"], fingerprint(self.registry))
        except (ValueError, OSError) as exc:
            self.feedback.setText(str(exc))
            return
        self.accept()
