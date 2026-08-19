"""Writing, AI connection and appearance preferences."""

from __future__ import annotations

import shlex

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.config import DEFAULT_CONFIG
from ui.theme import DARK_COLORS, LIGHT_COLORS


class SettingsDialog(QDialog):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("偏好设置")
        self.setMinimumSize(620, 640)
        self._config = dict(config)
        self._color_buttons: dict[str, QPushButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 14)
        heading = QLabel("偏好设置")
        heading.setObjectName("documentTitle")
        hint = QLabel("调整写作体验、AI 引擎连接与界面风格")
        hint.setObjectName("mutedLabel")
        root.addWidget(heading)
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 8, 8)

        writing_group = QGroupBox("写作")
        writing_form = QFormLayout(writing_group)
        self.auto_save_check = QCheckBox("定时保存正在编辑的文件")
        self.auto_save_check.setChecked(bool(self._config.get("auto_save", True)))
        writing_form.addRow("自动保存", self.auto_save_check)
        self.auto_save_interval = QSpinBox()
        self.auto_save_interval.setRange(5, 600)
        self.auto_save_interval.setSuffix(" 秒")
        self.auto_save_interval.setValue(int(self._config.get("auto_save_interval", 30)))
        writing_form.addRow("保存间隔", self.auto_save_interval)
        self.editor_font_spin = QSpinBox()
        self.editor_font_spin.setRange(12, 36)
        self.editor_font_spin.setValue(int(self._config.get("editor_font_size", 17)))
        writing_form.addRow("正文字号", self.editor_font_spin)
        content_layout.addWidget(writing_group)

        ai_group = QGroupBox("AI 引擎（dsh）")
        ai_form = QFormLayout(ai_group)
        self.command_edit = QLineEdit(str(self._config.get("dsh_command", "dsh")))
        self.command_edit.setPlaceholderText("dsh 或完整的可执行文件路径")
        ai_form.addRow("命令", self.command_edit)
        launcher_args = self._config.get("dsh_launcher_args") or []
        self.launcher_args_edit = QLineEdit(shlex.join(str(arg) for arg in launcher_args))
        self.launcher_args_edit.setPlaceholderText("例如：--yes @deepseek-ai/dsh")
        self.launcher_args_edit.setToolTip(
            "使用 npx 启动时填写 --yes @deepseek-ai/dsh；直接使用 dsh 时留空。"
        )
        ai_form.addRow("启动参数", self.launcher_args_edit)
        self.profile_edit = QLineEdit(str(self._config.get("dsh_profile", "headless")))
        ai_form.addRow("运行配置", self.profile_edit)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(30, 1800)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setValue(int(self._config.get("dsh_timeout", 180)))
        ai_form.addRow("最长等待", self.timeout_spin)
        content_layout.addWidget(ai_group)

        appearance_group = QGroupBox("外观")
        appearance_form = QFormLayout(appearance_group)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("墨夜深色", "dark")
        self.theme_combo.addItem("纸张浅色", "light")
        self.theme_combo.setCurrentIndex(0 if self._config.get("theme", "dark") == "dark" else 1)
        appearance_form.addRow("主题", self.theme_combo)
        self.ui_font_spin = QSpinBox()
        self.ui_font_spin.setRange(10, 22)
        self.ui_font_spin.setValue(int(self._config.get("ui_font_size", 14)))
        appearance_form.addRow("界面字号", self.ui_font_spin)

        color_row = QHBoxLayout()
        for key, label in (
            ("background_color", "背景"),
            ("panel_color", "面板"),
            ("field_color", "纸张"),
            ("text_color", "文字"),
            ("accent_color", "强调"),
        ):
            button = QPushButton(label)
            button.setMinimumHeight(36)
            button.clicked.connect(lambda _=False, k=key: self._choose_color(k))
            self._color_buttons[key] = button
            color_row.addWidget(button)
            self._paint_color_button(key)
        appearance_form.addRow("自定义色彩", color_row)
        content_layout.addWidget(appearance_group)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        restore = QPushButton("恢复默认外观")
        restore.clicked.connect(self._restore_defaults)
        bottom.addWidget(restore)
        bottom.addStretch(1)
        dialog_buttons = (
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons = QDialogButtonBox(dialog_buttons)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        root.addLayout(bottom)

        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.auto_save_check.toggled.connect(self.auto_save_interval.setEnabled)
        self.auto_save_interval.setEnabled(self.auto_save_check.isChecked())

    def _on_theme_changed(self, index: int) -> None:
        preset = LIGHT_COLORS if self.theme_combo.itemData(index) == "light" else DARK_COLORS
        for key, value in preset.items():
            self._config[key] = value
        for key in self._color_buttons:
            self._paint_color_button(key)

    def _choose_color(self, key: str) -> None:
        color = QColorDialog.getColor(QColor(self._config.get(key, "#000000")), self, "选择颜色")
        if color.isValid():
            self._config[key] = color.name().upper()
            self._paint_color_button(key)

    def _paint_color_button(self, key: str) -> None:
        button = self._color_buttons[key]
        value = self._config.get(key, DEFAULT_CONFIG[key])
        button.setToolTip(value)
        button.setStyleSheet(
            f"QPushButton {{ background-color: {value}; color: {self._contrast_text(value)}; }}"
        )

    def _restore_defaults(self) -> None:
        for key in DARK_COLORS:
            self._config[key] = DEFAULT_CONFIG[key]
        self._config["theme"] = DEFAULT_CONFIG["theme"]
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(0)
        self.theme_combo.blockSignals(False)
        self.ui_font_spin.setValue(int(DEFAULT_CONFIG["ui_font_size"]))
        self.editor_font_spin.setValue(int(DEFAULT_CONFIG["editor_font_size"]))
        for key in self._color_buttons:
            self._paint_color_button(key)

    def config(self) -> dict:
        cfg = dict(self._config)
        cfg.update(
            {
                "theme": self.theme_combo.currentData(),
                "ui_font_size": self.ui_font_spin.value(),
                "editor_font_size": self.editor_font_spin.value(),
                "auto_save": self.auto_save_check.isChecked(),
                "auto_save_interval": self.auto_save_interval.value(),
                "dsh_command": self.command_edit.text().strip() or "dsh",
                "dsh_launcher_args": shlex.split(self.launcher_args_edit.text()),
                "dsh_profile": self.profile_edit.text().strip() or "headless",
                "dsh_timeout": self.timeout_spin.value(),
            }
        )
        return cfg

    @staticmethod
    def _contrast_text(hex_color: str) -> str:
        color = QColor(hex_color)
        if not color.isValid():
            return "#000000"
        luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
        return "#17120D" if luminance > 145 else "#FFFFFF"
