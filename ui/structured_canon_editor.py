"""设定材料的结构化编辑对话框：表单 ⇄ 源码 ⇄ 预览三模式。

- 表单模式：按 schema 渲染分区；只有实际变化的分区会被规范化并加受管标记，
  其余分区保存时逐字节还原（core.canon_schema 的 round-trip 保证）。
- 源码模式：等同现有 md 编辑，整文件写回。
- 预览模式：只读 Markdown 渲染（不检索远程资源）。
- 修订锁：保存前校验文件自打开以来的字节指纹，外部修改必须重新打开。
- 索引模式（传入 project 时）：左侧列出全部设定材料，支持类型筛选与
  缺字段／同步状态／身份组徽章；切换材料时未保存修改可选择保存、放弃或取消；
  角色卡的“关键关系”分区提供按身份合并主名候选的关联选择器。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QStackedWidget,
    QButtonGroup, QVBoxLayout, QWidget)

from core.canon_schema import (
    CanonEntry, CanonSection, collect_canon_index, parse_canon_entry, render_canon_entry,
    schema_for, section_spec, set_section_field, set_group_field, set_section_text,
    ROLE_FIELDS, ROLE_GROUP, ROLE_MANAGED, ROLE_TEXT,
)
from core.character_identity import load_registry
from core.storage import atomic_write_text
from ui.editor import MarkdownPreview

MODE_FORM, MODE_SOURCE, MODE_PREVIEW = "form", "source", "preview"

# 索引筛选 chips：“全部”不过滤，其余按材料类型过滤
INDEX_FILTERS = (("all", "全部"), ("character", "角色"), ("world", "世界观"), ("power", "体系"))


def _index_item_text(item: dict) -> str:
    """索引条目显示文本：材料名 + 空格连接的状态徽章。"""
    badges: list[str] = []
    if item.get("kind") == "character":
        missing = int(item.get("missing_fields") or 0)
        if missing:
            badges.append(f"缺{missing}字段")
        badges.append("已同步" if item.get("synced") else "待同步")
    elif item.get("missing_sections"):
        badges.append(f"缺{item['missing_sections']}分区")
    if item.get("identity") == "primary":
        badges.append("身份组")
    elif item.get("identity") == "alias":
        badges.append("别名")
    return f"{item['name']} · " + " ".join(badges) if badges else item["name"]


class StructuredCanonEditor(QDialog):
    def __init__(self, path: Path | str, kind: str, project=None, parent=None):
        super().__init__(parent)
        self.project = project
        self.path = Path(path)
        self.kind = kind
        schema_for(kind)  # 打开即校验类型
        self._mode = MODE_FORM
        self._modified = False
        self._expected_hash = ""
        self.entry: CanonEntry | None = None  # 由 _load_entry 载入
        self._field_widgets: list[tuple[CanonSection, str, QLineEdit]] = []
        self._group_widgets: list[tuple[CanonSection, str, str, QLineEdit]] = []
        self._text_widgets: list[tuple[CanonSection, QPlainTextEdit]] = []
        self._relation_rows: list[tuple[CanonSection, QLineEdit]] = []
        self._alias_map: dict[str, str] = {}
        self._index: list[dict] = []

        screen = self.screen().availableGeometry()
        self.resize(min(720, screen.width() - 32), min(680, screen.height() - 32))

        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self._mode_buttons: dict[str, QPushButton] = {}
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        for mode, label in ((MODE_FORM, "表单"), (MODE_SOURCE, "源码"), (MODE_PREVIEW, "预览")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(mode == MODE_FORM)
            self._mode_group.addButton(button)
            self._mode_buttons[mode] = button
            self._mode_group.setId(button, {"form": 0, "source": 1, "preview": 2}[mode])
            bar.addWidget(button)
        bar.addStretch()
        layout.addLayout(bar)

        body = QHBoxLayout()
        if self.project is not None:
            self._alias_map = self._load_alias_map()
            body.addWidget(self._build_sidebar())
        self.stack = QStackedWidget(self)
        self.form_scroll = QScrollArea(self)
        self.form_scroll.setWidgetResizable(True)
        self.stack.addWidget(self.form_scroll)
        self.source_edit = QPlainTextEdit(self)
        self.source_edit.textChanged.connect(lambda: setattr(self, "_modified", True))
        self.stack.addWidget(self.source_edit)
        self.preview = MarkdownPreview(self)
        self.stack.addWidget(self.preview)
        body.addWidget(self.stack, stretch=1)
        layout.addLayout(body, stretch=1)

        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        save_button = buttons.addButton("保存", QDialogButtonBox.ButtonRole.AcceptRole)
        save_button.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._mode_group.idClicked.connect(self._switch_mode)
        self._load_entry(self.path, self.kind)

    # ---------- 索引侧栏（传入 project 时启用） ----------

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget(self)
        sidebar.setFixedWidth(200)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        chips = QHBoxLayout()
        chips.setSpacing(2)
        self._filter_buttons: dict[str, QPushButton] = {}
        filter_group = QButtonGroup(self)
        filter_group.setExclusive(True)
        for key, label in INDEX_FILTERS:
            button = QPushButton(label)
            button.setObjectName("indexFilterChip")
            button.setCheckable(True)
            button.setChecked(key == "all")
            filter_group.addButton(button)
            self._filter_buttons[key] = button
            button.clicked.connect(lambda _=False: self._populate_index())
            chips.addWidget(button)
        side.addLayout(chips)
        self._index_list = QListWidget(self)
        self._index_list.itemClicked.connect(self._on_index_clicked)
        side.addWidget(self._index_list, stretch=1)
        return sidebar

    def _load_alias_map(self) -> dict[str, str]:
        """从人物身份记录构建 别名 → 主名 映射；记录损坏时降级为空映射。"""
        try:
            registry = load_registry(self.project)
        except (ValueError, OSError):
            return {}
        return {name: group["primary"] for group in registry["groups"]
                for name in group["names"] if name != group["primary"]}

    def _current_filter(self) -> str:
        for key, button in self._filter_buttons.items():
            if button.isChecked():
                return key
        return "all"

    def _populate_index(self) -> None:
        """按当前筛选重建索引列表，并重新选中当前材料。"""
        self._index = collect_canon_index(self.project, self._alias_map)
        filter_key = self._current_filter()
        self._index_list.blockSignals(True)
        self._index_list.clear()
        for item in self._index:
            if filter_key != "all" and item["kind"] != filter_key:
                continue
            list_item = QListWidgetItem(_index_item_text(item))
            list_item.setData(Qt.ItemDataRole.UserRole,
                              {"path": str(item["path"]), "kind": item["kind"]})
            self._index_list.addItem(list_item)
        self._index_list.blockSignals(False)
        self._update_sidebar_selection()

    def _find_index_row(self, path: Path, kind: str) -> int | None:
        for row in range(self._index_list.count()):
            data = self._index_list.item(row).data(Qt.ItemDataRole.UserRole) or {}
            if data.get("kind") == kind and Path(str(data.get("path"))) == Path(path):
                return row
        return None

    def _update_sidebar_selection(self) -> None:
        """让索引选中项跟随当前材料；被当前筛选隐藏时回退“全部”。"""
        if self.project is None:
            return
        row = self._find_index_row(self.path, self.kind)
        if row is None:
            if self._current_filter() != "all":
                self._filter_buttons["all"].setChecked(True)
                self._populate_index()
            return
        self._index_list.blockSignals(True)
        self._index_list.setCurrentRow(row)
        self._index_list.blockSignals(False)

    def _on_index_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        try:
            target = Path(str(data["path"]))
            target_kind = str(data["kind"])
        except (KeyError, TypeError):
            return
        if target_kind == self.kind and target == self.path:
            return
        if self._modified:
            choice = self._confirm_switch()
            if choice == "cancel":
                self._update_sidebar_selection()
                return
            if choice == "save":
                ok, error = self._write_changes()
                if not ok:
                    self.feedback.setText(error)
                    self._update_sidebar_selection()
                    return
                self.feedback.clear()
                self._populate_index()  # 已保存材料的徽章需要刷新
        try:
            self._load_entry(target, target_kind)
        except (ValueError, OSError) as exc:
            self.feedback.setText(str(exc))
            self._update_sidebar_selection()

    def _confirm_switch(self) -> str:
        """未保存修改时的三选一确认：save（保存并切换）/ discard（放弃）/ cancel。"""
        box = QMessageBox(self)
        box.setWindowTitle("未保存的修改")
        box.setText(f"「{self.path.stem}」有未保存的修改，切换到其他设定材料前如何处理？")
        save_button = box.addButton("保存并切换", QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton("放弃修改", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_button:
            return "save"
        if clicked is discard_button:
            return "discard"
        return "cancel"

    # ---------- 载入 / 切换 ----------

    def _load_entry(self, path: Path | str, kind: str) -> None:
        """载入（或切换到）一份设定材料，并重置全部单文件状态。"""
        path = Path(path)
        original = path.read_bytes()  # 读取失败时抛出，原有状态保持不变
        # 与 Path.read_text 的通用换行语义一致：CRLF/CR 归一为 LF 后再解析
        text = original.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        entry = parse_canon_entry(kind, text)
        self.path = path
        self.kind = kind
        self._expected_hash = hashlib.sha256(original).hexdigest()
        self.entry = entry
        self._mode = MODE_FORM
        self._mode_buttons[MODE_FORM].setChecked(True)
        self.stack.setCurrentIndex(0)
        self.source_edit.clear()
        self._build_form()
        self.feedback.clear()
        self._modified = False
        self.setWindowTitle(f"结构化编辑 · {path.stem}")
        if self.project is not None:
            self._populate_index()

    # ---------- 表单构建 ----------

    def _build_form(self) -> None:
        self._field_widgets.clear()
        self._group_widgets.clear()
        self._text_widgets.clear()
        self._relation_rows.clear()
        content = QWidget()
        body = QVBoxLayout(content)
        body.setSpacing(12)
        for section in self.entry.sections:
            widget = self._section_widget(section)
            if widget is not None:
                body.addWidget(widget)
        body.addStretch()
        self.form_scroll.setWidget(content)

    def _section_widget(self, section: CanonSection):
        if section.role == ROLE_FIELDS:
            box = QGroupBox(section.heading)
            form = QFormLayout(box)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            declared = list(section_spec(self.kind, section.heading).get("keys", []))
            extra = [k for k in section.fields if k not in declared]
            for key in [*declared, *extra]:
                editor = QLineEdit(section.fields.get(key, ""))
                editor.textChanged.connect(lambda _text: setattr(self, "_modified", True))
                self._field_widgets.append((section, key, editor))
                form.addRow(key, editor)
            if section.extras:
                note = QLabel("含手写内容，保存时原样保留：" + "／".join(section.extras))
                note.setWordWrap(True)
                form.addRow(note)
            return box
        if section.role == ROLE_GROUP:
            box = QGroupBox(section.heading)
            vbox = QVBoxLayout(box)
            declared = list(section_spec(self.kind, section.heading).get("keys", []))
            for group in section.groups:
                group_box = QGroupBox(group.name)
                form = QFormLayout(group_box)
                form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                extra = [k for k in group.fields if k not in declared]
                for key in [*declared, *extra]:
                    editor = QLineEdit(group.fields.get(key, ""))
                    editor.textChanged.connect(lambda _text: setattr(self, "_modified", True))
                    self._group_widgets.append((section, group.name, key, editor))
                    form.addRow(key, editor)
                if group.extras:
                    note = QLabel("含手写内容，保存时原样保留：" + "／".join(group.extras))
                    note.setWordWrap(True)
                    form.addRow(note)
                vbox.addWidget(group_box)
            add_button = QPushButton("＋ 添加条目")
            add_button.clicked.connect(lambda _=False, s=section: self._add_group(s))
            vbox.addWidget(add_button)
            return box
        if section.role == ROLE_TEXT:
            if (self.project is not None and self.kind == "character"
                    and section.heading == "关键关系"):
                return self._relation_widget(section)
            box = QGroupBox(section.heading)
            vbox = QVBoxLayout(box)
            editor = QPlainTextEdit()
            editor.setPlainText(section.text)
            editor.setMaximumHeight(140)
            editor.textChanged.connect(lambda: setattr(self, "_modified", True))
            self._text_widgets.append((section, editor))
            vbox.addWidget(editor)
            return box
        if section.role == ROLE_MANAGED:
            # 只读分区不用 QGroupBox 标题：长标题会撑大最小宽度，窄窗口下溢出
            box = QGroupBox()
            vbox = QVBoxLayout(box)
            title = QLabel(section.heading)
            title.setObjectName("sectionTitle")
            title.setWordWrap(True)
            vbox.addWidget(title)
            body = section.raw.split("\n", 1)[-1].strip("\n")
            viewer = QPlainTextEdit()
            viewer.setPlainText(body)
            viewer.setReadOnly(True)
            viewer.setMaximumHeight(140)
            caption = QLabel("由 DeepSonder 自动同步，此处只读；请通过“同步角色档案”更新。")
            caption.setWordWrap(True)
            vbox.addWidget(viewer)
            vbox.addWidget(caption)
            return box
        # 自由备注 / 未知章节：不做结构化，源码模式编辑
        box = QGroupBox()
        vbox = QVBoxLayout(box)
        title = QLabel(section.heading or "未识别章节")
        title.setObjectName("sectionTitle")
        title.setWordWrap(True)
        vbox.addWidget(title)
        viewer = QPlainTextEdit()
        viewer.setPlainText(section.raw.strip("\n"))
        viewer.setReadOnly(True)
        viewer.setMaximumHeight(140)
        caption = QLabel("该分区不做结构化，请在源码模式中编辑。")
        caption.setWordWrap(True)
        vbox.addWidget(viewer)
        vbox.addWidget(caption)
        return box

    # ---------- 关键关系选择器（索引模式 + 角色卡） ----------

    def _relation_widget(self, section: CanonSection) -> QGroupBox:
        box = QGroupBox(section.heading)
        vbox = QVBoxLayout(box)
        for line in section.text.split("\n"):
            if line.strip():
                self._add_relation_row(section, vbox, line)
        add_button = QPushButton("＋ 关联角色")
        add_button.clicked.connect(lambda _=False, s=section, v=vbox: self._pick_relation(s, v))
        vbox.addWidget(add_button)
        return box

    def _add_relation_row(self, section: CanonSection, vbox: QVBoxLayout, text: str) -> None:
        editor = QLineEdit(text)
        editor.textChanged.connect(lambda _text: setattr(self, "_modified", True))
        remove = QPushButton("×")
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(editor, stretch=1)
        line.addWidget(remove)
        remove.clicked.connect(
            lambda _=False, e=editor, w=row: self._remove_relation_row(e, w))
        vbox.addWidget(row)
        self._relation_rows.append((section, editor))

    def _remove_relation_row(self, editor: QLineEdit, row: QWidget) -> None:
        self._relation_rows = [(s, e) for s, e in self._relation_rows if e is not editor]
        row.setParent(None)
        row.deleteLater()
        self._modified = True

    def _pick_relation(self, section: CanonSection, vbox: QVBoxLayout) -> None:
        candidates = self._relation_candidates()
        if not candidates:
            self.feedback.setText("项目内暂无其他角色可供关联。")
            return
        selected, ok = QInputDialog.getItem(self, "关联角色", "选择角色：", candidates, 0, False)
        if not ok or not selected:
            return
        self._add_relation_row(section, vbox, f"- {selected}：")
        self._modified = True
        self.feedback.clear()

    def _relation_candidates(self) -> list[str]:
        """身份合并后的主名候选（排除当前材料自身）。"""
        candidates: list[str] = []
        for path in self.project.list_characters():
            stem = path.stem
            primary = self._alias_map.get(stem, stem)
            if stem == self.path.stem or primary == self.path.stem:
                continue
            if primary not in candidates:
                candidates.append(primary)
        return sorted(candidates)

    def _add_group(self, section: CanonSection) -> None:
        name, ok = QInputDialog.getText(self, f"添加{section.heading}条目", "条目名称：")
        name = name.strip()
        if not ok or not name:
            return
        if any(g.name == name for g in section.groups):
            self.feedback.setText(f"条目「{name}」已存在。")
            return
        declared = list(section_spec(self.kind, section.heading).get("keys", []))
        set_group_field(self.entry, section.heading, name, declared[0] if declared else "备注", "")
        self.feedback.clear()
        self._build_form()

    # ---------- 模式切换 ----------

    def _switch_mode(self, button_id: int) -> None:
        mode = {0: MODE_FORM, 1: MODE_SOURCE, 2: MODE_PREVIEW}[button_id]
        if mode == self._mode:
            return
        previous = self._mode
        if previous == MODE_FORM:
            self._sync_form_to_entry()
            modified = self._modified
            self.source_edit.setPlainText(render_canon_entry(self.entry))
            self._modified = modified  # 回填源码触发的 textChanged 不算用户修改
        if mode == MODE_FORM:
            self.entry = parse_canon_entry(self.kind, self.source_edit.toPlainText())
            self._build_form()
        if mode == MODE_PREVIEW:
            self.preview.setMarkdown(self.source_edit.toPlainText())
        self._mode = mode
        self.stack.setCurrentIndex({"form": 0, "source": 1, "preview": 2}[mode])

    # ---------- 保存 ----------

    def _sync_form_to_entry(self) -> None:
        for section, key, editor in self._field_widgets:
            if editor.text() != section.fields.get(key, ""):
                set_section_field(self.entry, section.heading, key, editor.text())
        for section, group_name, key, editor in self._group_widgets:
            group = next(g for g in section.groups if g.name == group_name)
            if editor.text() != group.fields.get(key, ""):
                set_group_field(self.entry, section.heading, group_name, key, editor.text())
        for section, editor in self._text_widgets:
            if editor.toPlainText().strip("\n") != section.text:
                set_section_text(self.entry, section.heading, editor.toPlainText())
        # 关键关系选择器：按行拼接后写回
        relation_sections: list[CanonSection] = []
        for section, _editor in self._relation_rows:
            if not any(s is section for s in relation_sections):
                relation_sections.append(section)
        for section in relation_sections:
            joined = "\n".join(e.text() for s, e in self._relation_rows if s is section)
            if joined != section.text:
                set_section_text(self.entry, section.heading, joined)

    def _effective_text(self) -> str:
        if self._mode == MODE_FORM:
            self._sync_form_to_entry()
            return render_canon_entry(self.entry)
        return self.source_edit.toPlainText()

    def _write_changes(self) -> tuple[bool, str]:
        """执行保存（不关闭对话框）；返回 (是否成功, 错误信息)。"""
        current = self.path.read_bytes()
        if hashlib.sha256(current).hexdigest() != self._expected_hash:
            return False, "文件已在打开后被修改，为避免覆盖他人改动，请关闭后重新打开。"
        try:
            text = self._effective_text()
        except (ValueError, OSError) as exc:
            return False, str(exc)
        # 换行符差异不视为内容变化，避免对 CRLF 文件做无意义的整文件重写
        if text.replace("\r\n", "\n") == current.decode("utf-8").replace("\r\n", "\n"):
            self._modified = False
            return True, ""
        try:
            atomic_write_text(self.path, text, encoding="utf-8")
        except OSError as exc:
            return False, str(exc)
        self._modified = False
        return True, ""

    def save(self) -> None:
        ok, error = self._write_changes()
        if not ok:
            self.feedback.setText(error)
            return
        self.accept()
