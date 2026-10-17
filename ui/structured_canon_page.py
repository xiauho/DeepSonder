"""结构化设定材料页：嵌入主编辑区，与章节 md 编辑共用同一内容区域。

左侧导航树即索引入口；本页面承载单份材料的 表单 ⇄ 源码 ⇄ 预览 编辑，
页头提供 材料名 + 类型徽章 + 修改状态 + 保存按钮。md 仍是唯一事实源，
保存走 core.canon_schema 的字节保留 round-trip 与修订锁；切换材料或
离开页面前如有未保存修改，经 保存/放弃修改/取消 三选一确认。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from core.project_data import CANON_ENTRY_TYPES
from ui.canon_form_widget import CanonFormWidget
from ui.elided_label import ElidedLabel


class StructuredCanonPage(QWidget):
    """主编辑区中的结构化设定材料页面（替代弹窗作为设定材料的编辑视图）。"""

    saved = Signal(str)  # 实际写盘成功后发出（参数为保存路径）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("canonPage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("canonPageHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 18, 6)
        header_layout.setSpacing(8)

        self.title_label = ElidedLabel("未选择设定材料")
        self.title_label.setObjectName("documentTitle")
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header_layout.addWidget(self.title_label, 1)

        self.kind_badge = QLabel()
        self.kind_badge.setObjectName("canonKindBadge")
        self.kind_badge.hide()
        header_layout.addWidget(self.kind_badge)

        self.modified_badge = QLabel("未保存修改")
        self.modified_badge.hide()
        header_layout.addWidget(self.modified_badge)

        self.add_module_button = QPushButton("＋ 新建模块")
        self.add_module_button.setToolTip("在当前材料中新建自定义模块（选择名称与类型）")
        self.add_module_button.setEnabled(False)
        self.add_module_button.clicked.connect(self._prompt_new_module)
        header_layout.addWidget(self.add_module_button)

        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("accentButton")
        self.save_button.setToolTip("保存当前设定材料（Ctrl+S）")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        header_layout.addWidget(self.save_button)

        layout.addWidget(header)

        self.form = CanonFormWidget(self)
        self.form.on_saved = self._emit_saved
        self.form.modified_changed.connect(lambda _modified: self._sync_header())
        layout.addWidget(self.form, 1)

        self._sync_header()

    # ---------- 状态同步 ----------

    def _emit_saved(self, path) -> None:
        self.saved.emit(str(path))

    def _prompt_new_module(self) -> None:
        """页头“＋ 新建模块”入口：委托给表单的新建模块对话框。"""
        self.form.prompt_new_module()

    def _sync_header(self) -> None:
        path = self.form.current_path()
        kind = self.form.current_kind()
        title = self.form.current_title() or "未选择设定材料"
        self.title_label.setText(title)
        self.title_label.setToolTip(f"{title}\n{path}" if path is not None else title)
        self.kind_badge.setText(
            CANON_ENTRY_TYPES[kind]["label"] if kind in CANON_ENTRY_TYPES else "")
        self.kind_badge.setVisible(kind is not None)
        if path is None:
            self.modified_badge.hide()
        elif self.form.is_modified():
            self.modified_badge.setText("未保存修改")
            self.modified_badge.setObjectName("dirtyBadge")
        else:
            self.modified_badge.setText("已保存")
            self.modified_badge.setObjectName("savedBadge")
        self.modified_badge.setVisible(path is not None)
        self.modified_badge.style().unpolish(self.modified_badge)
        self.modified_badge.style().polish(self.modified_badge)
        self.save_button.setEnabled(path is not None)
        self.add_module_button.setEnabled(path is not None)

    # ---------- 对外接口 ----------

    def open_path(self, path: Path | str, kind: str, project=None) -> bool:
        """切换到指定设定材料；用户取消未保存确认时返回 False（保持旧材料）。

        载入失败时向上抛出异常，页面保持旧材料不变。
        """
        path = Path(path)
        if (self.form.current_path() is not None and kind == self.form.current_kind()
                and path == self.form.current_path()):
            self._sync_header()
            return True
        if self.form.is_modified() and not self.form.confirm_discard_or_save(self):
            return False
        self.form.load_entry(path, kind, project)
        self._sync_header()
        return True

    def save(self) -> None:
        """页头保存按钮：失败时弹出提示；成功经 saved 信号通知宿主。"""
        ok, error = self.write_changes()
        if not ok:
            QMessageBox.warning(self, "保存失败", error)

    def write_changes(self) -> tuple[bool, str]:
        return self.form.write_changes()

    def is_modified(self) -> bool:
        return self.form.is_modified()

    def current_path(self) -> Path | None:
        return self.form.current_path()

    def current_kind(self) -> str | None:
        return self.form.current_kind()

    def confirm_discard_or_save(self, parent=None) -> bool:
        return self.form.confirm_discard_or_save(parent)

    def reset(self) -> None:
        """清空当前材料（项目切换、材料被删除等场景），不做任何写盘。"""
        self.form.reset()
        self._sync_header()

    def set_theme(self, config: dict | None = None) -> None:
        self.form.set_theme(config)
