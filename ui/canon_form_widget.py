"""设定材料的结构化表单控件：表单 ⇄ 源码 ⇄ 预览三模式（可嵌入页面）。

从原结构化编辑对话框提取的文档表单核心，宿主可以是页面、对话框或分割器：
- 表单模式：按 schema 渲染分区；只有实际变化的分区会被规范化并加受管标记，
  其余分区保存时逐字节还原（core.canon_schema 的 round-trip 保证）。
- 源码模式：等同通用 md 编辑，整文件写回；切回表单时重新解析。
- 预览模式：只读 Markdown 渲染（不检索远程资源）。
- 修订锁：保存前校验文件自打开以来的字节指纹，外部修改必须重新打开。
- 未保存修改经 confirm_discard_or_save 提供 保存/放弃修改/取消 三选一，
  由宿主在切换材料或离开页面前调用；取消返回 False 表示中止操作。
- 角色卡的“关键关系”分区（传入 project 时）提供按身份合并主名候选的关联选择器。
- 非系统区块开放为用户模块：页头“＋ 新建模块”可新建（自定义名称与
  字段/文本/分组类型），每个模块标题行提供 重命名/上移/下移/删除；
  系统区块（基础档案、DeepSonder 同步区）结构受保护，只显示徽章。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QButtonGroup, QDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollArea, QStackedWidget, QToolButton,
    QVBoxLayout, QWidget)

from core.canon_schema import (
    CanonEntry, CanonSection, add_module, delete_module, is_system_section,
    move_module, parse_canon_entry, rename_module, render_canon_entry,
    schema_for, section_spec, set_section_field, set_group_field,
    set_section_text, ROLE_FIELDS, ROLE_GROUP, ROLE_MANAGED, ROLE_TEXT,
)
from core.character_identity import load_registry
from core.storage import atomic_write_text
from ui.editor import MarkdownPreview
from ui.theme import document_css

MODE_FORM, MODE_SOURCE, MODE_PREVIEW = "form", "source", "preview"


class NewModuleDialog(QDialog):
    """“新建模块”小对话框：名称 + 类型（字段型/文本型/分组型）。

    校验失败（重名、空名等）时在对话框内以错误标签提示，不关闭窗口；
    确认成功后调用表单的重建逻辑并 accept。
    """

    def __init__(self, form: "CanonFormWidget"):
        super().__init__(form)
        self.form = form
        self.setWindowTitle("新建模块")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)

        name_label = QLabel("名称：")
        layout.addWidget(name_label)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("模块名称")
        layout.addWidget(self.name_edit)

        type_label = QLabel("类型：")
        layout.addWidget(type_label)
        type_row = QHBoxLayout()
        self.role_buttons: dict[str, QRadioButton] = {}
        self._role_group = QButtonGroup(self)
        for role, text in ((ROLE_FIELDS, "字段型"), (ROLE_TEXT, "文本型"),
                           (ROLE_GROUP, "分组型")):
            radio = QRadioButton(text)
            radio.setChecked(role == ROLE_TEXT)
            self._role_group.addButton(radio)
            self.role_buttons[role] = radio
            type_row.addWidget(radio)
        type_row.addStretch()
        layout.addLayout(type_row)

        self.error_label = QLabel()
        self.error_label.setObjectName("saveErrorMessage")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        ok_button = QPushButton("确定")
        ok_button.setDefault(True)
        ok_button.clicked.connect(lambda _=False: self.confirm())
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(ok_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

    def selected_role(self) -> str:
        for role, radio in self.role_buttons.items():
            if radio.isChecked():
                return role
        return ROLE_TEXT

    def confirm(self) -> None:
        """确定：校验并新建模块；失败时在对话框内提示，不关闭窗口。"""
        if self.form.entry is None:
            self.error_label.setText("尚未打开设定材料。")
            self.error_label.show()
            return
        try:
            add_module(self.form.entry, self.name_edit.text(), self.selected_role())
        except ValueError as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            return
        self.form.feedback.clear()
        self.form._build_form()
        self.form._mark_modified()
        self.accept()


class CanonFormWidget(QWidget):
    """单份设定材料的结构化表单；不感知宿主页面，也不负责导航。"""

    modified_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.path: Path | None = None
        self.kind: str | None = None
        self._on_saved = None  # 实际写盘成功后回调（参数为保存路径）
        self._mode = MODE_FORM
        self._modified = False
        self._expected_hash = ""
        self.entry: CanonEntry | None = None  # 由 load_entry 载入
        self._field_widgets: list[tuple[CanonSection, str, QLineEdit]] = []
        self._group_widgets: list[tuple[CanonSection, str, str, QLineEdit]] = []
        self._text_widgets: list[tuple[CanonSection, QPlainTextEdit]] = []
        self._relation_rows: list[tuple[CanonSection, QLineEdit]] = []
        self._alias_map: dict[str, str] = {}
        self._theme_config: dict = {"theme": "light"}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 12)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        self._mode_buttons: dict[str, QToolButton] = {}
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        for mode, label in ((MODE_FORM, "表单"), (MODE_SOURCE, "源码"), (MODE_PREVIEW, "预览")):
            button = QToolButton()
            button.setText(label)
            button.setObjectName("editorModeButton")
            button.setCheckable(True)
            button.setChecked(mode == MODE_FORM)
            self._mode_group.addButton(button)
            self._mode_buttons[mode] = button
            self._mode_group.setId(button, {"form": 0, "source": 1, "preview": 2}[mode])
            bar.addWidget(button)
        bar.addStretch()
        layout.addLayout(bar)

        self.stack = QStackedWidget(self)
        self.form_scroll = QScrollArea(self)
        self.form_scroll.setObjectName("pageScroll")
        self.form_scroll.setWidgetResizable(True)
        self.stack.addWidget(self.form_scroll)
        self.source_edit = QPlainTextEdit(self)
        self.source_edit.setObjectName("writingEditor")
        self.stack.addWidget(self.source_edit)
        self.preview = MarkdownPreview(self)
        self.preview.setObjectName("markdownPreview")
        self.preview.document().setDefaultStyleSheet(document_css(self._theme_config))
        self.stack.addWidget(self.preview)
        layout.addWidget(self.stack, stretch=1)

        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)

        self.source_edit.textChanged.connect(self._mark_modified)
        self._mode_group.idClicked.connect(self._switch_mode)
        self._build_form()  # 初始为空表单，等待 load_entry

    # ---------- 对外状态 ----------

    @property
    def on_saved(self):
        """实际写盘成功后的回调（参数为保存路径）；无变化的保存不回调。"""
        return self._on_saved

    @on_saved.setter
    def on_saved(self, callback) -> None:
        self._on_saved = callback

    def current_path(self) -> Path | None:
        return self.path

    def current_kind(self) -> str | None:
        return self.kind

    def current_title(self) -> str:
        if self.entry is None:
            return ""
        return self.entry.title or (self.path.stem if self.path else "")

    def is_modified(self) -> bool:
        return self._modified

    def set_theme(self, config: dict | None = None) -> None:
        """跟随应用主题更新预览文档样式。"""
        self._theme_config = dict(config or {"theme": "light"})
        self.preview.document().setDefaultStyleSheet(document_css(self._theme_config))

    def reset(self) -> None:
        """清空当前材料（项目切换等场景），不触发任何确认或写盘。"""
        self.project = None
        self.path = None
        self.kind = None
        self.entry = None
        self._expected_hash = ""
        self._alias_map = {}
        self._field_widgets.clear()
        self._group_widgets.clear()
        self._text_widgets.clear()
        self._relation_rows.clear()
        self._build_form()
        self._fill_source("")
        self.preview.clear()
        self.feedback.clear()
        self._set_mode(MODE_FORM)
        self._set_modified(False)

    def load_entry(self, path: Path | str, kind: str, project=None) -> None:
        """载入（或切换到）一份设定材料，并重置全部单文件状态。

        读取或解析失败时抛出异常，原有状态保持不变。
        """
        path = Path(path)
        schema_for(kind)  # 打开即校验类型
        original = path.read_bytes()
        # 与 Path.read_text 的通用换行语义一致：CRLF/CR 归一为 LF 后再解析
        text = original.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        entry = parse_canon_entry(kind, text)
        self.project = project
        self._alias_map = self._load_alias_map() if project is not None else {}
        self.path = path
        self.kind = kind
        self._expected_hash = hashlib.sha256(original).hexdigest()
        self.entry = entry
        self._fill_source("")
        self._build_form()
        self.feedback.clear()
        self._set_mode(MODE_FORM)
        self._set_modified(False)

    def confirm_discard_or_save(self, parent=None) -> bool:
        """未保存修改时的 保存/放弃修改/取消 三选一。

        返回 True 表示可以继续（已保存或已放弃）；取消或保存失败返回 False。
        """
        if not self._modified:
            return True
        box = QMessageBox(parent if parent is not None else self)
        box.setWindowTitle("未保存的修改")
        box.setText(f"「{self.path.stem}」有未保存的修改，如何处理？")
        save_button = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton("放弃修改", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is discard_button:
            # 放弃：从磁盘重载当前材料，内存中的修改随之丢弃
            try:
                self.load_entry(self.path, self.kind, self.project)
            except (ValueError, OSError):
                self.reset()  # 文件已不可读（如被删除）：清空表单
            return True
        if clicked is not save_button:
            return False  # 取消：保持现状
        ok, error = self.write_changes()
        if not ok:
            self.feedback.setText(error)
            return False
        return True

    def write_changes(self) -> tuple[bool, str]:
        """执行保存（不切换材料）；返回 (是否成功, 错误信息)。

        实际写盘成功后回调 on_saved（内容未变化的无操作保存不回调）。
        """
        if self.path is None or self.entry is None:
            return False, "尚未打开设定材料。"
        try:
            current = self.path.read_bytes()
        except OSError as exc:
            return False, f"无法读取原文件，请重新打开后再保存：{exc}"
        if hashlib.sha256(current).hexdigest() != self._expected_hash:
            return False, "文件已在打开后被修改，为避免覆盖他人改动，请重新打开。"
        try:
            text = self._effective_text()
        except (ValueError, OSError) as exc:
            return False, str(exc)
        # 换行符差异不视为内容变化，避免对 CRLF 文件做无意义的整文件重写
        if text.replace("\r\n", "\n") == current.decode("utf-8").replace("\r\n", "\n"):
            self._set_modified(False)
            return True, ""
        try:
            atomic_write_text(self.path, text, encoding="utf-8")
        except OSError as exc:
            return False, str(exc)
        self._set_modified(False)
        if self._on_saved is not None:
            self._on_saved(self.path)
        return True, ""

    # ---------- 内部状态 ----------

    def _set_modified(self, value: bool) -> None:
        value = bool(value)
        if value != self._modified:
            self._modified = value
            self.modified_changed.emit(value)

    def _mark_modified(self) -> None:
        self._set_modified(True)

    def _fill_source(self, text: str) -> None:
        # 回填源码触发的 textChanged 不算用户修改
        modified = self._modified
        self.source_edit.setPlainText(text)
        self._set_modified(modified)

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._mode_buttons[mode].setChecked(True)
        self.stack.setCurrentIndex({"form": 0, "source": 1, "preview": 2}[mode])

    def _load_alias_map(self) -> dict[str, str]:
        """从人物身份记录构建 别名 → 主名 映射；记录损坏时降级为空映射。"""
        try:
            registry = load_registry(self.project)
        except (ValueError, OSError):
            return {}
        return {name: group["primary"] for group in registry["groups"]
                for name in group["names"] if name != group["primary"]}

    # ---------- 表单构建 ----------

    def _build_form(self) -> None:
        self._field_widgets.clear()
        self._group_widgets.clear()
        self._text_widgets.clear()
        self._relation_rows.clear()
        content = QWidget()
        body = QVBoxLayout(content)
        body.setSpacing(12)
        if self.entry is not None:
            for section in self.entry.sections:
                widget = self._section_widget(section)
                if widget is not None:
                    body.addWidget(widget)
        body.addStretch()
        self.form_scroll.setWidget(content)

    def _section_widget(self, section: CanonSection):
        if section.role == ROLE_MANAGED:
            return self._managed_widget(section)
        body = self._section_body(section)
        if body is None:
            return None
        return self._wrap_module(section, body)

    def _wrap_module(self, section: CanonSection, body: QWidget) -> QGroupBox:
        """模块区块：标题行 + 系统徽章或操作按钮（重命名/上移/下移/删除）。

        不用 QGroupBox 标题承载长标题：长标题会撑大最小宽度，窄窗口溢出。
        """
        box = QGroupBox()
        vbox = QVBoxLayout(box)
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        title = QLabel(section.heading or "未识别章节")
        title.setObjectName("sectionTitle")
        title.setWordWrap(True)
        row.addWidget(title, 1)
        if is_system_section(self.entry, section):
            badge = QLabel("系统区块")
            badge.setObjectName("systemModuleBadge")
            row.addWidget(badge)
        else:
            row.addWidget(self._module_op_buttons(section))
        vbox.addWidget(header)
        vbox.addWidget(body)
        return box

    def _module_op_buttons(self, section: CanonSection) -> QWidget:
        ops = QWidget()
        row = QHBoxLayout(ops)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for text, name, handler in (
            ("重命名", "moduleOpButton", lambda _=False, s=section: self._rename_module(s)),
            ("上移", "moduleOpButton", lambda _=False, s=section: self._move_module(s, -1)),
            ("下移", "moduleOpButton", lambda _=False, s=section: self._move_module(s, 1)),
            ("删除", "moduleDeleteButton", lambda _=False, s=section: self._delete_module(s)),
        ):
            button = QPushButton(text)
            button.setObjectName(name)
            button.setProperty("moduleHeading", section.heading)
            button.clicked.connect(handler)
            row.addWidget(button)
        return ops

    def _section_body(self, section: CanonSection):
        if section.role == ROLE_FIELDS:
            body = QWidget()
            form = QFormLayout(body)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            spec = section_spec(self.kind, section.heading)
            declared = list(spec.get("keys", [])) if spec else []
            extra = [k for k in section.fields if k not in declared]
            for key in [*declared, *extra]:
                editor = QLineEdit(section.fields.get(key, ""))
                editor.textChanged.connect(lambda _text: self._mark_modified())
                self._field_widgets.append((section, key, editor))
                form.addRow(key, editor)
            if section.extras:
                note = QLabel("含手写内容，保存时原样保留：" + "／".join(section.extras))
                note.setWordWrap(True)
                form.addRow(note)
            return body
        if section.role == ROLE_GROUP:
            body = QWidget()
            vbox = QVBoxLayout(body)
            spec = section_spec(self.kind, section.heading)
            declared = list(spec.get("keys", [])) if spec else []
            for group in section.groups:
                group_box = QGroupBox(group.name)
                form = QFormLayout(group_box)
                form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                extra = [k for k in group.fields if k not in declared]
                for key in [*declared, *extra]:
                    editor = QLineEdit(group.fields.get(key, ""))
                    editor.textChanged.connect(lambda _text: self._mark_modified())
                    self._group_widgets.append((section, group.name, key, editor))
                    form.addRow(key, editor)
                if group.extras:
                    note = QLabel("含手写内容，保存时原样保留：" + "／".join(group.extras))
                    note.setWordWrap(True)
                    form.addRow(note)
                vbox.addWidget(group_box)
            add_button = QPushButton("＋ 添加条目")
            add_button.setProperty("moduleHeading", section.heading)
            add_button.clicked.connect(lambda _=False, s=section: self._add_group(s))
            vbox.addWidget(add_button)
            return body
        if section.role == ROLE_TEXT:
            if (self.project is not None and self.kind == "character"
                    and section.heading == "关键关系"):
                return self._relation_body(section)
            editor = QPlainTextEdit()
            editor.setPlainText(section.text)
            editor.setMaximumHeight(140)
            editor.textChanged.connect(lambda: self._mark_modified())
            self._text_widgets.append((section, editor))
            return editor
        # 自由备注 / 未识别章节：不做结构化，源码模式编辑
        body = QWidget()
        vbox = QVBoxLayout(body)
        viewer = QPlainTextEdit()
        viewer.setPlainText(section.raw.strip("\n"))
        viewer.setReadOnly(True)
        viewer.setMaximumHeight(140)
        caption = QLabel("该分区不做结构化，请在源码模式中编辑。")
        caption.setWordWrap(True)
        vbox.addWidget(viewer)
        vbox.addWidget(caption)
        return body

    def _managed_widget(self, section: CanonSection) -> QGroupBox:
        # 受管区不用 QGroupBox 标题：长标题会撑大最小宽度，窄窗口下溢出
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

    # ---------- 关键关系选择器（角色卡 + 传入 project 时） ----------

    def _relation_body(self, section: CanonSection) -> QWidget:
        """关键关系分区主体（标题与模块操作按钮由 _wrap_module 提供）。"""
        body = QWidget()
        vbox = QVBoxLayout(body)
        vbox.setContentsMargins(0, 0, 0, 0)
        for line in section.text.split("\n"):
            if line.strip():
                self._add_relation_row(section, vbox, line)
        add_button = QPushButton("＋ 关联角色")
        add_button.clicked.connect(lambda _=False, s=section, v=vbox: self._pick_relation(s, v))
        vbox.addWidget(add_button)
        return body

    def _add_relation_row(self, section: CanonSection, vbox: QVBoxLayout, text: str) -> None:
        editor = QLineEdit(text)
        editor.textChanged.connect(lambda _text: self._mark_modified())
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
        self._mark_modified()

    def _pick_relation(self, section: CanonSection, vbox: QVBoxLayout) -> None:
        candidates = self._relation_candidates()
        if not candidates:
            self.feedback.setText("项目内暂无其他角色可供关联。")
            return
        selected, ok = QInputDialog.getItem(self, "关联角色", "选择角色：", candidates, 0, False)
        if not ok or not selected:
            return
        self._add_relation_row(section, vbox, f"- {selected}：")
        self._mark_modified()
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
        spec = section_spec(self.kind, section.heading)
        declared = list(spec.get("keys", [])) if spec else []
        set_group_field(self.entry, section.heading, name, declared[0] if declared else "备注", "")
        self.feedback.clear()
        self._build_form()

    # ---------- 用户模块操作（新建/重命名/上移/下移/删除） ----------

    def prompt_new_module(self) -> NewModuleDialog | None:
        """打开“新建模块”对话框（页头“＋ 新建模块”按钮入口）。"""
        if self.entry is None:
            self.feedback.setText("尚未打开设定材料。")
            return None
        dialog = NewModuleDialog(self)
        dialog.exec()
        return dialog

    def _rename_module(self, section: CanonSection) -> None:
        text, ok = QInputDialog.getText(self, "重命名模块", "新名称：", text=section.heading)
        if not ok:
            return
        try:
            rename_module(self.entry, section.heading, text)
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        self.feedback.clear()
        self._build_form()
        self._mark_modified()

    def _move_module(self, section: CanonSection, offset: int) -> None:
        try:
            move_module(self.entry, section.heading, offset)
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        self.feedback.clear()
        self._build_form()
        self._mark_modified()

    def _delete_module(self, section: CanonSection) -> None:
        answer = QMessageBox.question(
            self, "删除模块",
            f"删除模块「{section.heading}」？其内容将在保存时从 md 文档中移除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_module(self.entry, section.heading)
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        self.feedback.clear()
        self._build_form()
        self._mark_modified()

    # ---------- 模式切换 ----------

    def _switch_mode(self, button_id: int) -> None:
        mode = {0: MODE_FORM, 1: MODE_SOURCE, 2: MODE_PREVIEW}[button_id]
        if mode == self._mode:
            return
        previous = self._mode
        if previous == MODE_FORM:
            self._sync_form_to_entry()
            self._fill_source(render_canon_entry(self.entry))
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
