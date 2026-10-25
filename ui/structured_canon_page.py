"""结构化设定材料页：嵌入主编辑区，与章节 md 编辑共用同一内容区域。

左侧导航树即索引入口；本页面承载单份材料的结构化表单编辑（仅表单，
md 源码完全后台化），页头提供 材料名 + 类型徽章 + 修改状态 + 保存按钮。
md 仍是唯一事实源，保存走 core.canon_schema 的字节保留 round-trip 与
修订锁；切换材料或离开页面前如有未保存修改，经 保存/放弃修改/取消
三选一确认。
"""
from __future__ import annotations

from pathlib import Path
from difflib import unified_diff

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMenu, QMessageBox,
    QPlainTextEdit,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from core.project_data import CANON_ENTRY_TYPES
from core.storage import atomic_write_text
from ui.canon_form_widget import CanonFormWidget
from ui.canon_find import CanonFindBar, CanonFindController
from ui.canon_layout import CanonModuleHeader
from ui.elided_label import ElidedLabel
from ui.icons import set_button_icon, refresh_button_icons
from ui.theme import colors_for


class StructuredCanonPage(QWidget):
    """主编辑区中的结构化设定材料页面（替代弹窗作为设定材料的编辑视图）。"""

    reloaded = Signal(str)
    saved = Signal(str)  # 实际写盘成功后发出（参数为保存路径）
    sync_requested = Signal(str)
    chapter_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("canonPage")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("canonPageHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(24, 14, 24, 14)
        header_layout.setSpacing(8)
        toolbar = QWidget()
        toolbar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(8)

        self.title_label = ElidedLabel("未选择设定材料")
        self.title_label.setObjectName("documentTitle")
        self.title_label.setMinimumWidth(0)
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self.kind_badge = QLabel()
        self.kind_badge.setObjectName("canonKindBadge")
        self.kind_badge.hide()
        toolbar_layout.addWidget(self.kind_badge)

        self.outline_toggle = QToolButton()
        self.outline_toggle.setObjectName("outlineToggleButton")
        set_button_icon(self.outline_toggle, "toc", size=17)
        self.outline_toggle.setCheckable(True)
        self.outline_toggle.setChecked(True)
        self.outline_toggle.setAccessibleName("分区大纲")
        self.outline_toggle.setToolTip("显示或隐藏分区大纲；窄窗口下改为弹出分区列表")
        self.outline_toggle.clicked.connect(self._toggle_outline)
        toolbar_layout.addWidget(self.outline_toggle)

        self.add_module_button = QPushButton("＋ 新建模块")
        self.add_module_button.setToolTip("在当前材料中新建自定义模块（选择名称与类型）")
        self.add_module_button.setEnabled(False)
        self.add_module_button.clicked.connect(self._prompt_new_module)
        toolbar_layout.addWidget(self.add_module_button)

        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("accentButton")
        self.save_button.setToolTip("保存当前设定材料（Ctrl+S）")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save)
        toolbar_layout.addWidget(self.save_button)

        adaptive_header = CanonModuleHeader(self.title_label, toolbar)
        adaptive_header.setObjectName("canonPageToolbar")
        header_layout.addWidget(adaptive_header)
        # 状态独占固定的一行，长度和颜色变化不再挤动标题及操作按钮。
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        self.modified_badge = QLabel("已保存")
        self.modified_badge.setAccessibleName("资料保存状态")
        policy = self.modified_badge.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.modified_badge.setSizePolicy(policy)
        status_row.addWidget(self.modified_badge)
        status_row.addStretch()
        header_layout.addLayout(status_row)
        layout.addWidget(header)

        self._saved_revision = None
        self._saved_feedback_timer = QTimer(self)
        self._saved_feedback_timer.setSingleShot(True)
        self._saved_feedback_timer.setInterval(3000)
        self._saved_feedback_timer.timeout.connect(self._sync_header)

        # 与章节编辑器共用同一项自动保存设置；关闭时保留显式保存与离开确认。
        self._auto_save_enabled = False
        self._auto_save_interval_ms = 30_000
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.timeout.connect(self._auto_save_flush)

        self.form = CanonFormWidget(self)
        self.find_bar = CanonFindBar(self)
        self.find_controller = CanonFindController(self, self.form, self.find_bar)
        layout.addWidget(self.find_bar)
        self.form.on_saved = self._emit_saved
        self.form.reloaded.connect(self.reloaded)
        self.form.modified_changed.connect(self._modified_changed)
        self.form.modified_changed.connect(self._schedule_auto_save)
        self.form.state_changed.connect(self._sync_header)
        self.form.outline_changed.connect(self._refresh_outline)
        self.form.current_module_changed.connect(self._set_outline_current)
        self.form.sync_requested.connect(self._request_sync)
        self.form.chapter_requested.connect(self._request_chapter)

        # 页内大纲：宽窗口常驻在表单右侧，窄窗口收进页头的分区按钮。
        self._outline_requested = True
        self._outline_entries: list[tuple[str, str, bool]] = []
        self._outline_current = ""
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)
        body_row.addWidget(self.form, 1)
        self.outline_panel = QFrame()
        self.outline_panel.setObjectName("canonOutlinePanel")
        outline_layout = QVBoxLayout(self.outline_panel)
        outline_layout.setContentsMargins(10, 12, 12, 12)
        outline_layout.setSpacing(6)
        self.outline_title = QLabel("分区")
        self.outline_title.setObjectName("canonOutlineTitle")
        self.outline_title.setToolTip("● 有内容 · ○ 空白；不代表模板填写完整")
        outline_layout.addWidget(self.outline_title)
        self.outline_list = QListWidget()
        self.outline_list.setObjectName("canonOutlineList")
        self.outline_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.outline_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.outline_list.setAccessibleName("资料分区大纲")
        self.outline_list.setAccessibleDescription("● 有内容 · ○ 空白；不代表模板填写完整")
        self.outline_list.itemClicked.connect(self._on_outline_item_clicked)
        self.outline_list.itemActivated.connect(self._on_outline_item_clicked)
        outline_layout.addWidget(self.outline_list, 1)
        self.outline_panel.setMinimumWidth(150)
        self.outline_panel.setMaximumWidth(210)
        self.outline_panel.hide()
        body_row.addWidget(self.outline_panel)
        layout.addLayout(body_row, 1)

        # 恢复类操作只在版本冲突或保存失败时浮现，不常驻页面底部。
        self.recovery_bar = QWidget()
        recovery = QHBoxLayout(self.recovery_bar)
        recovery.setContentsMargins(18, 0, 18, 8)
        self.diff_button = QPushButton("查看差异")
        self.diff_button.clicked.connect(self.show_revision_diff)
        recovery.addWidget(self.diff_button)
        self.draft_button = QPushButton("另存草稿")
        self.draft_button.clicked.connect(self.save_draft_copy)
        recovery.addWidget(self.draft_button)
        recovery.addStretch()
        self.reload_button = QPushButton("重新载入")
        self.reload_button.setToolTip("从磁盘重新载入当前资料；有未保存内容时会先确认")
        self.reload_button.clicked.connect(self.reload_from_disk)
        recovery.addWidget(self.reload_button)
        layout.addWidget(self.recovery_bar)

        self._sync_header()

    # ---------- 状态同步 ----------

    def show_find(self) -> None:
        self.find_controller.show()

    def hide_find(self) -> None:
        self.find_controller.close()

    def _emit_saved(self, path) -> None:
        self._saved_revision = (str(path), self.form._expected_hash)
        self._saved_feedback_timer.start()
        self._sync_header()
        self.saved.emit(str(path))

    def _modified_changed(self, modified: bool) -> None:
        if modified:
            self._saved_feedback_timer.stop()
        self._sync_header()

    def _request_sync(self, source: str) -> None:
        if self.current_kind() == "character" and str(self.current_path()) == source:
            self.sync_requested.emit(source)

    def _request_chapter(self, source: str, chapter_id: str) -> None:
        if str(self.current_path()) == source:
            self.chapter_requested.emit(chapter_id)

    def set_sync_available(self, available: bool, reason: str = "") -> None:
        self.form.set_sync_available(available, reason)

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
        if self._saved_revision != (str(path), self.form._expected_hash):
            self._saved_feedback_timer.stop()
        if path is not None and self.form.external_changed:
            state = ("外部版本变化", "saveErrorBadge")
        elif path is not None and self.form.save_error:
            state = ("保存失败", "saveErrorBadge")
        elif path is not None and self.form.is_modified():
            state = ("待保存", "dirtyBadge")
        elif path is not None:
            state = (("保存成功", "savedBadge") if self._saved_feedback_timer.isActive()
                     else ("已保存", "canonSavedBadge"))
        else:
            state = None
        if state is None:
            self.modified_badge.hide()
        else:
            self.modified_badge.setText(state[0])
            self.modified_badge.setObjectName(state[1])
            self.modified_badge.setAccessibleName(f"资料保存状态：{state[0]}")
            self.modified_badge.setToolTip(self.form.save_error or state[0])
            self.modified_badge.show()
        if self.form.external_changed or self.form.save_error:
            self._saved_feedback_timer.stop()
        self.modified_badge.style().unpolish(self.modified_badge)
        self.modified_badge.style().polish(self.modified_badge)
        self.save_button.setEnabled(path is not None)
        self.add_module_button.setEnabled(path is not None)
        self.reload_button.setEnabled(path is not None)
        self.diff_button.setVisible(self.form.external_changed)
        self.draft_button.setVisible(bool(self.form.external_changed or self.form.save_error))
        self.recovery_bar.setVisible(bool(self.form.external_changed or self.form.save_error))

    # ---------- 页内大纲 ----------

    OUTLINE_MIN_WIDTH = 900

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_outline_visibility()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._update_outline_visibility()

    def _toggle_outline(self) -> None:
        """宽窗口切换常驻大纲；窄窗口改为弹出分区列表，避免挤压表单。"""
        if self.width() >= self.OUTLINE_MIN_WIDTH:
            self._outline_requested = self.outline_toggle.isChecked()
        else:
            self._show_outline_popup()
        self._update_outline_visibility()

    def _update_outline_visibility(self) -> None:
        visible = self._outline_requested and self.width() >= self.OUTLINE_MIN_WIDTH
        self.outline_panel.setVisible(visible)
        self.outline_toggle.setChecked(visible)
        if visible:
            self._refresh_outline()

    def outline_entries(self) -> list[tuple[str, str, bool]]:
        """当前大纲（实时读取表单输入）：(module_id, 标题, 是否有内容)。"""
        return self.form.outline_entries()

    def _refresh_outline(self) -> None:
        entries = self.form.outline_entries()
        if entries == self._outline_entries:
            return
        self._outline_entries = entries
        self.outline_list.blockSignals(True)
        self.outline_list.clear()
        for module_id, heading, filled in entries:
            item = QListWidgetItem(f"{'●' if filled else '○'} {heading}")
            item.setData(Qt.ItemDataRole.UserRole, module_id)
            status = "有内容" if filled else "空白"
            item.setToolTip(f"{heading}\n{status}")
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{heading}，{status}")
            self.outline_list.addItem(item)
        self.outline_list.blockSignals(False)
        self._set_outline_current(self._outline_current if entries else "")

    def _set_outline_current(self, module_id: str) -> None:
        self._outline_current = module_id
        for row in range(self.outline_list.count()):
            if self.outline_list.item(row).data(Qt.ItemDataRole.UserRole) == module_id:
                self.outline_list.setCurrentRow(row)
                return
        self.outline_list.setCurrentRow(-1)

    def _on_outline_item_clicked(self, item) -> None:
        self.form.reveal_module(item.data(Qt.ItemDataRole.UserRole) or "")

    def _show_outline_popup(self) -> None:
        menu = QMenu(self.outline_toggle)
        menu.setToolTipsVisible(True)
        entries = self.form.outline_entries()
        if not entries:
            menu.addAction("尚未打开设定材料").setEnabled(False)
        for module_id, heading, filled in entries:
            action = menu.addAction(("● " if filled else "○ ") + heading)
            action.setToolTip(f"{heading}\n{'有内容' if filled else '空白'}")
            action.triggered.connect(
                lambda _checked=False, target=module_id: self.form.reveal_module(target))
        menu.exec(self.outline_toggle.mapToGlobal(self.outline_toggle.rect().bottomLeft()))
        menu.deleteLater()

    # ---------- 自动保存 ----------

    def configure_auto_save(self, enabled: bool, interval_seconds: int) -> None:
        """与章节编辑器共用自动保存设置；关闭时回退到显式保存与离开确认。"""
        self._auto_save_enabled = bool(enabled)
        self._auto_save_interval_ms = max(5, int(interval_seconds or 30)) * 1000
        self._schedule_auto_save(self.form.is_modified())

    def auto_save_enabled(self) -> bool:
        return self._auto_save_enabled

    def _schedule_auto_save(self, modified: bool) -> None:
        if modified and self._auto_save_enabled:
            self._auto_save_timer.start(self._auto_save_interval_ms)
        else:
            self._auto_save_timer.stop()

    def _auto_save_flush(self) -> None:
        """定时写盘；失败时保留草稿并继续按间隔重试，不弹打断式对话框。"""
        if not self.form.is_modified():
            return
        ok, _error = self.form.write_changes()
        if not ok and self.form.is_modified():
            self._auto_save_timer.start(self._auto_save_interval_ms)

    # ---------- 对外接口 ----------

    def open_path(self, path: Path | str, kind: str, project=None) -> bool:
        """切换到指定设定材料；用户取消未保存确认时返回 False（保持旧材料）。

        载入失败时向上抛出异常，页面保持旧材料不变。
        """
        path = Path(path)
        if (self.form.current_path() is not None and kind == self.form.current_kind()
                and path == self.form.current_path()):
            self.form.refresh_from_disk()
            self._sync_header()
            return True
        if not self.prepare_leave(self):
            return False
        self.form.load_entry(path, kind, project)
        self._sync_header()
        return True

    def refresh_from_disk(self) -> bool:
        return self.form.refresh_from_disk()

    def reload_from_disk(self) -> bool:
        path, kind = self.current_path(), self.current_kind()
        if path is None:
            return False
        if self.is_modified():
            answer = QMessageBox.question(
                self, "重新载入资料", "重新载入会放弃当前未保存的内容。需要保留时，请先另存草稿。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            self.form.load_entry(path, kind, self.form.project)
            self.reloaded.emit(str(path))
        except (OSError, ValueError) as exc:
            self.form._save_failed(str(exc), external=True)
            QMessageBox.warning(self, "重新载入失败", str(exc))
            return False
        return True

    def export_draft(self, destination: Path | str) -> tuple[bool, str]:
        """Export a copy without changing the loaded revision or clearing the draft."""
        if self.current_path() is None:
            return False, "尚未打开设定材料。"
        try:
            destination = Path(destination)
            if destination.resolve() == self.current_path().resolve():
                return False, "请为草稿选择其他文件，原资料不会被覆盖。"
            atomic_write_text(destination, self.form._effective_text(), encoding="utf-8")
        except (OSError, ValueError) as exc:
            return False, str(exc)
        return True, ""

    def save_draft_copy(self) -> None:
        path = self.current_path()
        if path is None:
            return
        destination, _filter = QFileDialog.getSaveFileName(
            self, "另存资料草稿", str(path.with_name(path.stem + "-草稿.md")),
            "Markdown (*.md)")
        if destination:
            ok, error = self.export_draft(destination)
            if not ok:
                QMessageBox.warning(self, "另存草稿失败", error)
            else:
                self.form.feedback.setText("草稿副本已保存；当前资料的版本冲突仍需处理。")

    def show_revision_diff(self) -> None:
        if self.current_path() is None:
            return
        try:
            disk = self.current_path().read_bytes().decode("utf-8")
            draft = self.form._effective_text()
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "无法查看差异", str(exc))
            return
        baseline = self.form._baseline_text
        external_diff = "".join(unified_diff(baseline.splitlines(keepends=True), disk.splitlines(keepends=True),
                                             fromfile="打开时版本", tofile="当前磁盘版本"))
        local_diff = "".join(unified_diff(baseline.splitlines(keepends=True), draft.splitlines(keepends=True),
                                         fromfile="打开时版本", tofile="本地草稿"))
        diff = "外部变化：\n" + (external_diff or "无内容变化。\n") + "\n本地修改：\n" + (local_diff or "无内容变化。\n")
        dialog = QDialog(self)
        dialog.setWindowTitle("资料版本差异")
        dialog.resize(720, 520)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("仅查看差异；不会覆盖磁盘版本或修改本地草稿。"))
        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(diff or "当前内容与磁盘版本一致。")
        layout.addWidget(viewer)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()
        dialog.deleteLater()

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

    def prepare_leave(self, parent=None) -> bool:
        """离开当前资料前处理未保存修改。

        自动保存开启时先静默写盘，写盘失败才退回三选一确认；自动保存关闭时
        保持原有的 保存/放弃修改/取消 交互。
        """
        if not self.form.is_modified():
            return True
        if self._auto_save_enabled and self.form.write_changes()[0]:
            return True
        return self.confirm_discard_or_save(parent)

    def reset(self) -> None:
        """清空当前材料（项目切换、材料被删除等场景），不做任何写盘。"""
        self._auto_save_timer.stop()
        self._saved_feedback_timer.stop()
        self._saved_revision = None
        self.form.reset()
        self._sync_header()

    def set_theme(self, config: dict | None = None) -> None:
        self.form.set_theme(config)
        muted = str(colors_for(config).get("muted_text_color", "#63748A"))
        set_button_icon(self.outline_toggle, "toc", muted, size=17)
        refresh_button_icons(self.form, muted)
        refresh_button_icons(self.find_bar, muted)
        if self.find_controller._active:
            self.find_controller._highlight_current()
