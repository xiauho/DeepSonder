"""设定材料的结构化表单控件：仅表单视图，md 源码完全后台化（可嵌入页面）。

从原结构化编辑对话框提取的文档表单核心，宿主可以是页面、对话框或分割器：
- 表单按 schema 渲染分区；只有实际变化的分区会被规范化并加受管标记，
  其余分区保存时逐字节还原（core.canon_schema 的 round-trip 保证）。
- 不再提供源码/预览模式：自由备注与无法结构化的未识别小节以可编辑的
  “原文区块”呈现（正文整块编辑、修改后原样逐字写回），字段/分组模块
  内的手写行同样直接可编辑；任何内容都有编辑入口。
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
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QEvent, QFileSystemWatcher, QPoint, QTimer, Signal, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from core.canon_schema import (
    CanonEntry, CanonSection, add_module, delete_module, is_system_section,
    preamble_blocks, set_preamble_text, set_section_suffix,
    move_module, parse_canon_entry, raw_section_body, render_canon_entry,
    rename_module, schema_for, section_spec, set_group_extras,
    set_group_field, set_raw_section_text, set_section_extras,
    set_section_field, set_section_text, ROLE_FIELDS, ROLE_FREE, ROLE_GROUP,
    ROLE_MANAGED, ROLE_RAW, ROLE_TEXT,
)
from core.canon_fields import (add_field, rename_field, delete_field, move_field,
    add_group, rename_group, delete_group, move_group)
from core.character_identity import load_registry
from core.storage import atomic_write_text
from ui.wrapping_label import WrappingLabel
from ui.icons import set_button_icon
from ui.canon_sync_view import CanonSyncView
from ui.theme import colors_for
from ui.canon_layout import CanonCard, CanonFieldsGrid, CanonIntroCard, CanonModuleHeader, CanonTextEdit
from ui.canon_history import CanonEditHistory, CanonSnapshot, ordered_equal


# 空分区写作引导：让作者在打开一份空白资料时就知道每个分区该写什么。
# 未登记的分区按角色回退到通用提示，不显示与内容无关的说明。
SECTION_HINTS: dict[str, str] = {
    "核心规则": "这个世界不变的底层规则。例：灵气的来源与枯竭条件、生死的界限。",
    "地理与空间": "主要地点、区域关系与彼此的距离感。例：北境与中原之间隔着断魂岭。",
    "历史与现状": "影响当下的关键历史事件，以及故事开始时各方所处的局面。",
    "社会、势力与秩序": "主要势力、权力结构与阶层规则。例：宗门按境界分配资源。",
    "技术、魔法或特殊现象": "这一世界的特殊能力与现象如何运作，以及它的边界。",
    "体系定位": "这套体系在故事中解决什么问题，与其他体系如何区分。",
    "等级或层级": "分几层、每层的名称与代表能力。例：炼气 → 筑基 → 金丹。",
    "核心原则": "体系运转的基本逻辑。例：同等境界下法宝决定胜负。",
    "获取方式": "角色如何得到它、需要满足什么条件。",
    "使用限制": "什么时候不能用、什么情况下会失效。",
    "代价与副作用": "使用的成本与反噬。例：越级施法会损伤经脉。",
    "克制关系": "谁克制谁，以及克制成立的原因。",
    "晋升与突破": "升级需要满足的条件与仪式，以及失败会怎样。",
    "对剧情的约束": "因此不能出现的情节，避免后续写崩。例：凡人无法直接对抗修士。",
    "已知例外": "打破规则的个别情况，需要交代成立原因。",
    "已知例外与未解问题": "暂时说不通或刻意留白的地方，供后续埋伏笔。",
    "空间层级": "空间分几层，每层各自的特征与用途。",
    "空间之间的关系": "各空间如何连通或彼此隔离。",
    "进入与离开条件": "满足什么条件才能进出，以及代价。",
    "时间流速差异": "不同空间的时间比例。例：内一日，外一年。",
    "空间边界与限制": "边界在哪里、有什么硬性限制。",
    "空间资源": "空间内可获取的资源与稀缺程度。",
    "与主世界的关系": "它对主世界的影响与联系。",
    "全书不可违背的规则": "整本书都不能违反的硬约束。例：死者不可复生。",
    "基础约束条件": "规则生效的前提。例：只有持剑者能触发。",
    "生效与获取条件": "生效需要满足什么、又如何获得。",
    "全局例外": "唯一的例外及其成立条件。",
    "外貌与辨识特征": "可被反复引用的外观细节。例：左手腕有旧疤。",
    "性格、动机与核心欲望": "他想要什么、害怕什么，冲突从哪里来。",
    "长期目标与人物弧线": "这个角色从哪走到哪，关键转折在什么时候。",
    "关键关系": "与其他角色的关系，以及这层关系里的张力。",
    "秘密与人物弧线": "尚未揭晓的信息与揭露时机。",
    "能力档案": "这项能力如何运作、有哪些限制与代价。",
    "作者自由备注": "不属于模板分区的补充信息，保存时原样保留。",
}

# 模板预设字段的示例提示，只在输入框为空时显示。
FIELD_HINTS: dict[str, str] = {
    "姓名": "主名，同时作为角色卡文件名",
    "别名": "其他称呼，用「、」分隔",
    "年龄／出生信息": "例：27 岁／春天出生",
    "性别": "例：女",
    "身份／阵营": "例：守夜人 · 北境",
    "首次出场": "例：第 3 章",
    "所属体系": "例：御剑体系",
    "能力属性": "例：火／风／神识",
    "当前等级": "例：筑基三层",
    "当前数值": "例：攻击 120",
    "使用条件": "例：需持剑，且灵力充足",
    "限制与代价": "例：每日最多三次",
    "克制关系": "例：被水系克制",
}


def section_hint(heading: str, role: str) -> str:
    """分区占位提示：优先取登记文案，否则按角色给出保守的通用提示。"""
    hint = SECTION_HINTS.get(heading)
    if hint:
        return hint
    if role == ROLE_TEXT:
        return "填写这一分区的内容；保存后写回原 Markdown。"
    if role in (ROLE_RAW, ROLE_FREE):
        return "写下不属于模板分区的内容，保存时原样保留。"
    return ""


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
            self.form._change_structure(
                lambda draft: add_module(draft, self.name_edit.text(), self.selected_role()))
        except ValueError as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            return
        self.accept()


class CanonFormWidget(QWidget):
    """单份设定材料的结构化表单；不感知宿主页面，也不负责导航。"""

    modified_changed = Signal(bool)
    state_changed = Signal()
    reloaded = Signal(str)
    outline_changed = Signal()
    current_module_changed = Signal(str)
    sync_requested = Signal(str)
    chapter_requested = Signal(str, str)  # 来源资料路径、章节 id
    history_changed = Signal()
    form_about_to_rebuild = Signal()
    form_rebuilt = Signal()
    document_replaced = Signal()
    view_layout_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project = None
        self.path: Path | None = None
        self.kind: str | None = None
        self._on_saved = None  # 实际写盘成功后回调（参数为保存路径）
        self._modified = False
        self._last_editor = None
        self._expected_hash = ""
        self.external_changed = False
        self.save_error = ""
        self._baseline_text = ""
        self.entry: CanonEntry | None = None  # 由 load_entry 载入
        self._preamble_widgets: list[tuple[int, QPlainTextEdit]] = []
        self._suffix_widgets: list[tuple[CanonSection, QPlainTextEdit]] = []
        self._field_widgets: list[tuple[CanonSection, str, QLineEdit]] = []
        self._group_widgets: list[tuple[CanonSection, str, str, QLineEdit]] = []
        self._text_widgets: list[tuple[CanonSection, QPlainTextEdit]] = []
        self._raw_widgets: list[tuple[CanonSection, QPlainTextEdit]] = []
        self._extras_rows: list[tuple[CanonSection, str | None, list[QLineEdit]]] = []
        self._relation_rows: list[tuple[CanonSection, QLineEdit]] = []
        self._relation_sections: list[CanonSection] = []
        self._alias_map: dict[str, str] = {}
        self._theme_config: dict = {"theme": "light"}
        self._sync_available = True
        self._sync_unavailable_reason = ""
        self._collapse_by_path: dict[str, set[str]] = {}
        # 跨会话折叠记忆钩子：由主窗口注入，按文档路径读取/回写折叠的模块 id。
        self.collapse_state_loader = None  # Callable[[Path], set[str] | None]
        self.collapse_state_saver = None   # Callable[[Path, set[str]], None]
        self._module_bodies: dict[str, QWidget] = {}
        self._module_toggles: dict[str, QToolButton] = {}
        self._heading_toggles: dict[QWidget, QToolButton] = {}
        self.history = CanonEditHistory()
        self._history_restoring = False
        self._history_building = False
        self._history_typing = None
        self._search_labels = {}
        self._search_expanded = set()
        self._search_navigating = False
        self._intro_card = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 8, 18, 8)
        layout.setSpacing(8)

        self.form_scroll = QScrollArea(self)
        self.form_scroll.setObjectName("pageScroll")
        self.form_scroll.setWidgetResizable(True)
        layout.addWidget(self.form_scroll, stretch=1)

        self.feedback = QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)

        self._pending_view_restore = None
        self._view_restore_timer = QTimer(self)
        self._view_restore_timer.setSingleShot(True)
        self._view_restore_timer.timeout.connect(self._restore_pending_view)
        self._watcher = QFileSystemWatcher(self)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self.refresh_from_disk)
        self._watcher.fileChanged.connect(lambda _path: self._refresh_timer.start())
        self._watcher.directoryChanged.connect(lambda _path: self._refresh_timer.start())
        # 分区大纲的内容状态从实时输入推导，避免每击键重建。
        self._outline_timer = QTimer(self)
        self._outline_timer.setSingleShot(True)
        self._outline_timer.setInterval(300)
        self._outline_timer.timeout.connect(self.outline_changed)
        self._current_module = ""
        # 显式定位的目标可能无法贴顶（例如最后一个分区）；保留选择直到用户滚动。
        self._navigation_target: str | None = None
        self._navigation_timer = QTimer(self)
        self._navigation_timer.setSingleShot(True)
        self._navigation_timer.timeout.connect(self._scroll_to_navigation_target)
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(16)
        self._layout_timer.timeout.connect(self._settle_visual_layout)
        bar = self.form_scroll.verticalScrollBar()
        bar.valueChanged.connect(self._track_current_module)
        bar.actionTriggered.connect(self._resume_outline_tracking)
        bar.sliderPressed.connect(self._resume_outline_tracking)
        for widget in (self.form_scroll, self.form_scroll.viewport(), bar):
            widget.installEventFilter(self)
        QApplication.instance().focusChanged.connect(self._remember_focus)
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

    def outline_entries(self) -> list[tuple[str, str, bool]]:
        """分区大纲：(module_id, 标题, 是否有内容)；资料导语用空 module_id 表示。

        有内容不代表模板填写完整。状态读取当前输入控件，因此未保存的编辑也能反映；
        没有对应输入控件的分区（例如只读同步区）按已载入的数据判断。
        """
        if self.entry is None:
            return []
        values: dict[tuple[int, str], dict[str, str]] = {}
        for section, key, editor in self._field_widgets:
            values.setdefault((id(section), ""), {})[key] = editor.text()
        for section, group, key, editor in self._group_widgets:
            values.setdefault((id(section), group), {})[key] = editor.text()
        extras: dict[tuple[int, str], list[str]] = {}
        for section, group, rows in self._extras_rows:
            extras[(id(section), group or "")] = [row.text() for row in rows]
        text_live = {id(section): editor.toPlainText() for section, editor in self._text_widgets}
        # 关系选择器没有普通文本框，最后一行被删除后也必须以空草稿覆盖旧文本。
        text_live.update({id(section): "\n".join(
            editor.text() for owner, editor in self._relation_rows if owner is section)
            for section in self._relation_sections})
        raw_live = {id(section): editor.toPlainText() for section, editor in self._raw_widgets}

        def fields_filled(section: CanonSection, group: str | None) -> bool:
            holder = (next(g for g in section.groups if g.name == group)
                      if group is not None else section)
            # 预设字段即使还没有写入文件，也已有输入控件，必须纳入实时状态。
            fields = {**holder.fields, **values.get((id(section), group or ""), {})}
            if any(value.strip() for value in fields.values()):
                return True
            return any(line.strip() for line in extras.get((id(section), group or ""), []))

        entries: list[tuple[str, str, bool]] = [
            ("", "资料导语", any(e.toPlainText().strip() for _index, e in self._preamble_widgets))
        ]
        for section in self.entry.sections:
            if section.role == ROLE_TEXT:
                live = text_live.get(id(section))
                filled = bool((str(live) if live is not None else section.text).strip())
            elif section.role in (ROLE_RAW, ROLE_FREE):
                live = raw_live.get(id(section))
                body = raw_section_body(section) if live is None else str(live)
                filled = bool(body.strip())
            elif section.role == ROLE_FIELDS:
                filled = fields_filled(section, None)
            elif section.role == ROLE_GROUP:
                filled = fields_filled(section, None) or any(
                    fields_filled(section, group.name) for group in section.groups)
            elif section.role == ROLE_MANAGED:
                filled = bool(raw_section_body(section).strip())
            else:
                filled = False
            entries.append((section.module_id, section.heading or "未识别章节", filled))
        return entries

    def reveal_module(self, module_id: str) -> bool:
        """展开并滚动到指定分区；空 module_id 表示回到资料导语。

        对齐标题行；页底无法贴顶时仍选中目标，用户主动滚动后恢复位置跟踪。
        """
        content = self.form_scroll.widget()
        if content is None:
            return False
        if module_id and module_id not in self._module_bodies:
            return False
        self._navigation_target = module_id
        if module_id:
            self._module_toggles[module_id].setChecked(True)
        elif self._intro_card is not None:
            self._intro_card.expand()
        self._scroll_to_navigation_target()
        # 展开正文后的滚动范围由 Qt 延迟更新，再定位一次以保证标题可见。
        self._navigation_timer.start(0)
        return True

    def _scroll_to_navigation_target(self) -> None:
        target = self._navigation_target
        content = self.form_scroll.widget()
        if target is None or content is None:
            return
        layout = content.layout()
        if layout is not None:
            layout.activate()
        bar = self.form_scroll.verticalScrollBar()
        toggle = self._module_toggles.get(target)
        # 标题换行后，箭头在标题行内垂直居中；以整行顶部定位，避免裁掉首行。
        offset = toggle.parentWidget().mapTo(content, QPoint(0, 0)).y() if toggle is not None else 0
        bar.setValue(max(0, min(offset, bar.maximum())))
        # 即使滚动值没有变化，也要更新选择（相邻页底分区可能共享同一滚动值）。
        self._set_current_module(target)

    def _resume_outline_tracking(self, _action=0) -> None:
        self._navigation_target = None
        self._navigation_timer.stop()
        self._track_current_module()

    def set_theme(self, config: dict | None = None) -> None:
        """记录应用主题配置（表单配色由全局样式表驱动）。"""
        self._theme_config = dict(config or {"theme": "light"})

    def reset(self) -> None:
        """清空当前材料（项目切换等场景），不触发任何确认或写盘。"""
        self.document_replaced.emit()
        self._search_expanded.clear()
        self._refresh_timer.stop()
        self._view_restore_timer.stop()
        self._pending_view_restore = None
        watched = self._watcher.files() + self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)
        self._collapse_by_path.clear()
        self.project = None
        self.path = None
        self.kind = None
        self.entry = None
        self._expected_hash = ""
        self.external_changed = False
        self.save_error = ""
        self._baseline_text = ""
        self._alias_map = {}
        self.history.clear()
        self._preamble_widgets.clear()
        self._suffix_widgets.clear()
        self._field_widgets.clear()
        self._group_widgets.clear()
        self._text_widgets.clear()
        self._raw_widgets.clear()
        self._extras_rows.clear()
        self._relation_rows.clear()
        self._relation_sections.clear()
        self._build_form()
        self.feedback.clear()
        self._set_modified(False)
        self.history_changed.emit()
        self.state_changed.emit()

    def load_entry(self, path: Path | str, kind: str, project=None) -> None:
        """载入（或切换到）一份设定材料，并重置全部单文件状态。

        读取或解析失败时抛出异常，原有状态保持不变。
        """
        path = Path(path)
        schema_for(kind)  # 打开即校验类型
        original = path.read_bytes()
        text = original.decode("utf-8")
        entry = parse_canon_entry(kind, text)
        self.document_replaced.emit()
        self._search_expanded.clear()
        self.project = project
        self._alias_map = self._load_alias_map() if project is not None else {}
        self.path = path
        self.kind = kind
        self._expected_hash = hashlib.sha256(original).hexdigest()
        self._baseline_text = original.decode("utf-8")
        self.external_changed = False
        self.save_error = ""
        watched = self._watcher.files() + self._watcher.directories()
        if watched:
            self._watcher.removePaths(watched)
        self._watcher.addPaths([str(path), str(path.parent)])
        self.entry = entry
        self._build_form()
        self.history.clear(self._history_snapshot())
        self.history_changed.emit()
        self.feedback.clear()
        self._set_modified(False)
        self.state_changed.emit()

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

    def refresh_from_disk(self) -> bool:
        """Refresh a clean document; an external revision must never replace a draft."""
        if self.path is None:
            return True
        try:
            if self.path.is_file() and str(self.path) not in self._watcher.files():
                self._watcher.addPath(str(self.path))
            current = self.path.read_bytes()
            if hashlib.sha256(current).hexdigest() == self._expected_hash:
                if self.external_changed:
                    self.external_changed = False
                    self.save_error = ""
                    self.feedback.clear()
                    self.state_changed.emit()
                return True
            if not self.is_modified():
                self.load_entry(self.path, self.kind, self.project)
                self.reloaded.emit(str(self.path))
                return True
            error = "原资料已有新版本，本地修改已保留。请查看差异、另存草稿，或重新载入。"
        except (OSError, ValueError) as exc:
            error = f"原文件暂时无法读取，本地内容已保留，请重新载入或另存草稿：{exc}"
        self._save_failed(error, external=True)
        return False

    def _save_failed(self, error: str, *, external: bool = False) -> tuple[bool, str]:
        self.external_changed = self.external_changed or external
        self.save_error = error
        self.feedback.setText(error)
        self.state_changed.emit()
        return False, error

    def _commit_saved(self, draft: CanonEntry, data: bytes) -> None:
        # Keep widget section references alive; only replace their collected data.
        self.entry.preamble = draft.preamble
        self.entry.title = draft.title
        for section, saved in zip(self.entry.sections, draft.sections):
            if saved.dirty:
                saved.raw = render_canon_entry(CanonEntry(
                    kind=draft.kind, title="", preamble="", sections=[saved]))
                saved.marked = True
            saved.dirty = False
            section.__dict__.update(saved.__dict__)
        self._expected_hash = hashlib.sha256(data).hexdigest()
        self._baseline_text = data.decode("utf-8")
        self.external_changed = False
        self.save_error = ""
        self.feedback.clear()
        self._set_modified(False)
        self.history.replace_current(self._history_snapshot())
        self.history_changed.emit()
        self.state_changed.emit()

    def write_changes(self) -> tuple[bool, str]:
        """Commit collected input and update the revision only after a successful write."""
        if self.path is None or self.entry is None:
            return False, "尚未打开设定材料。"
        self.history.break_merge()
        try:
            current = self.path.read_bytes()
        except OSError as exc:
            return self._save_failed(f"无法读取原文件，本地修改已保留，请重新载入或另存草稿：{exc}", external=True)
        if hashlib.sha256(current).hexdigest() != self._expected_hash:
            return self._save_failed(
                "原资料已有新版本，尚未覆盖。本地修改已保留，请查看差异、另存草稿或重新载入。",
                external=True)
        try:
            draft = self._collected_entry()
            text = render_canon_entry(draft)
            unchanged = text.encode("utf-8") == current
            if not unchanged:
                atomic_write_text(self.path, text, encoding="utf-8")
        except (ValueError, OSError) as exc:
            return self._save_failed(str(exc))
        self._commit_saved(draft, current if unchanged else text.encode("utf-8"))
        if not unchanged and self._on_saved is not None:
            self._on_saved(self.path)
        return True, ""

    # ---------- 内部状态 ----------

    def _restore_pending_view(self) -> None:
        callback = self._pending_view_restore
        self._pending_view_restore = None
        if callback is not None:
            callback()

    def _editor_bindings(self):
        yield from ((None, ("intro", i), e) for i, e in self._preamble_widgets)
        yield from ((s, ("suffix",), e) for s, e in self._suffix_widgets)
        yield from ((s, ("field", k), e) for s, k, e in self._field_widgets)
        yield from ((s, ("group", g, k), e) for s, g, k, e in self._group_widgets)
        yield from ((s, ("text",), e) for s, e in self._text_widgets)
        yield from ((s, ("raw",), e) for s, e in self._raw_widgets)
        yield from ((s, ("relation", i), e) for i, (s, e) in enumerate(self._relation_rows))
        for section, group, rows in self._extras_rows:
            yield from ((section, ("extras", group, i), e) for i, e in enumerate(rows))

    def _editable_widgets(self):
        return [editor for _section, _key, editor in self._editor_bindings()]

    def search_targets(self):
        """Visible author content in document order, addressed by stable module id."""
        bindings = list(self._editor_bindings())
        for section, key, editor in bindings:
            if section is None:
                yield (None, key), editor
        if self.entry is None:
            return
        for section in self.entry.sections:
            identity = section.module_id
            heading = (identity, ("heading",))
            yield heading, self._search_labels[heading]
            rows = [(key, editor) for s, key, editor in bindings if s is section]

            def field_target(key, editor):
                if key[0] in {"field", "group"}:
                    group = key[1] if key[0] == "group" else None
                    label_key = (identity, ("field-label", group, key[-1]))
                    yield label_key, self._search_labels[label_key]
                yield (identity, key), editor

            if section.role == ROLE_GROUP:
                for key, editor in rows:
                    if key[0] == "extras" and key[1] is None:
                        yield (identity, key), editor
                for group in section.groups:
                    label_key = (identity, ("group-label", group.name))
                    yield label_key, self._search_labels[label_key]
                    for key, editor in rows:
                        if key[0] in {"group", "extras"} and key[1] == group.name:
                            yield from field_target(key, editor)
            else:
                for key, editor in rows:
                    if key[0] != "suffix":
                        yield from field_target(key, editor)
            if section.role == ROLE_MANAGED:
                for view in self._module_bodies[identity].findChildren(CanonSyncView) + (
                        [self._module_bodies[identity]] if isinstance(self._module_bodies[identity], CanonSyncView) else []):
                    for key, value in view.value_labels.items():
                        yield (identity, ("sync-label", key)), view.caption_labels[key]
                        yield (identity, ("sync-value", key)), value
            for key, editor in rows:
                if key[0] == "suffix":
                    yield (identity, key), editor

    def _remember_focus(self, _old, current) -> None:
        if not self._history_restoring and not self._history_building and _old is not current:
            self.history.break_merge()
        if any(current is editor for editor in self._editable_widgets()):
            self._last_editor = current
            self._navigation_target = None
            self._navigation_timer.stop()

    def eventFilter(self, watched, event) -> bool:
        """标题行整行可点击折叠；标题行内的开关仍各自处理自己的点击。"""
        if watched in self._editable_widgets():
            if event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
                command = ("undo" if event.matches(QKeySequence.StandardKey.Undo) else
                           "redo" if event.matches(QKeySequence.StandardKey.Redo) else None)
                if command:
                    event.accept()
                    if event.type() == QEvent.Type.KeyPress:
                        self.edit_command(command)
                    return True
            if event.type() == QEvent.Type.KeyPress:
                self.history.update_view(self._history_view(watched))
                modifiers = event.modifiers()
                typing = (bool(event.text()) and not modifiers & (
                    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
                    and event.key() not in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                           Qt.Key.Key_Tab, Qt.Key.Key_Backspace, Qt.Key.Key_Delete))
                deleting = event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete)
                selected = (bool(watched.selectedText()) if isinstance(watched, QLineEdit)
                            else watched.textCursor().hasSelection())
                if selected:
                    self.history.break_merge()
                self._history_typing = (watched, "insert" if typing else event.key()) if (
                    typing or (deleting and not selected)) else None
                if self._history_typing is None:
                    self.history.break_merge()
            elif event.type() == QEvent.Type.KeyRelease:
                self._history_typing = None
            elif event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusOut):
                self._history_typing = None
                self.history.break_merge()
                self.history.update_view(self._history_view(watched))
            elif event.type() == QEvent.Type.InputMethod:
                # One committed IME phrase is one step; preedit text is not saved.
                self._history_typing = None
                self.history.break_merge()
        if watched in (self.form_scroll, self.form_scroll.viewport(),
                       self.form_scroll.verticalScrollBar()):
            if (event.type() == QEvent.Type.Wheel
                    or (event.type() == QEvent.Type.KeyPress and event.key() in {
                        Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp,
                        Qt.Key.Key_PageDown, Qt.Key.Key_Home, Qt.Key.Key_End,
                        Qt.Key.Key_Space})):
                self._resume_outline_tracking()
        toggle = self._heading_toggles.get(watched)
        if (toggle is not None
                and event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and watched.childAt(event.position().toPoint()) is not toggle):
            toggle.setChecked(not toggle.isChecked())
            return True
        return super().eventFilter(watched, event)

    def focus_editor(self):
        editors = self._editable_widgets()
        editor = next((e for e in editors if e is self._last_editor), None)
        if editor is None:
            editor = next(iter(editors), None)
        if editor is not None:
            self._reveal_editor(editor)
            editor.setFocus()
        return editor

    def edit_command(self, command: str) -> None:
        if command in {"undo", "redo"}:
            self._history_step(-1 if command == "undo" else 1)
            return
        editor = next((e for e in self._editable_widgets() if e is self._last_editor), None)
        if editor is not None:
            self._reveal_editor(editor)
            editor.setFocus()
            getattr(editor, command)()

    def _reveal_editor(self, editor) -> None:
        if self._intro_card is not None and editor in [e for _i, e in self._preamble_widgets]:
            self._intro_card.expand()
            self.form_scroll.ensureWidgetVisible(editor)
            return
        for identity, body in self._module_bodies.items():
            if body is editor or body.isAncestorOf(editor):
                self._module_toggles[identity].setChecked(True)
                self.form_scroll.ensureWidgetVisible(editor)
                break

    def _history_view(self, focus=None):
        focus = focus if focus is not None else self._last_editor
        target = None
        for section, key, editor in self._editor_bindings():
            if editor is focus:
                if isinstance(editor, QLineEdit):
                    anchor = editor.selectionStart()
                    position = editor.cursorPosition()
                    if anchor >= 0:
                        end = anchor + len(editor.selectedText().encode("utf-16-le")) // 2
                        anchor = end if position == anchor else anchor
                    else:
                        anchor = position
                else:
                    position, anchor = editor.textCursor().position(), editor.textCursor().anchor()
                target = (section.module_id if section else None, key, position, anchor)
                break
        return {"focus": target, "scroll": self.form_scroll.verticalScrollBar().value()}

    def _history_snapshot(self):
        values = {(section.module_id if section else None, key):
                  editor.text() if isinstance(editor, QLineEdit) else editor.toPlainText()
                  for section, key, editor in self._editor_bindings()}
        return CanonSnapshot(deepcopy(self.entry), values, self._history_view())

    def _bind_history_editor(self, editor):
        editor.installEventFilter(self)
        editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        editor.customContextMenuRequested.connect(lambda point, e=editor: self._editor_menu(e, point))
        editor.textChanged.connect(lambda *_, e=editor: self._input_history_changed(e))
        if isinstance(editor, QLineEdit):
            editor.cursorPositionChanged.connect(lambda *_: self._remember_history_cursor(editor))
        else:
            editor.cursorPositionChanged.connect(lambda: self._remember_history_cursor(editor))
            # No second, widget-local history can accumulate behind the session.
            editor.setUndoRedoEnabled(False)

    def _remember_history_cursor(self, editor):
        if not self._history_restoring and not self._history_building and not self._search_navigating:
            self.history.update_view(self._history_view(editor))

    def _input_history_changed(self, editor):
        if self._history_restoring or self._history_building:
            return
        self._last_editor = editor
        key = next(((s.module_id if s else None, k) for s, k, e in self._editor_bindings()
                    if e is editor), None)
        merge = (key, self._history_typing[1]) if (
            self._history_typing is not None and self._history_typing[0] is editor) else None
        self._record_history(merge)

    def _record_history(self, merge=None):
        if self._history_restoring or self._history_building or self.entry is None:
            return
        self.history.record(self._history_snapshot(), merge)
        self._sync_history_dirty()
        self.history_changed.emit()

    def _sync_history_dirty(self):
        try:
            modified = self._effective_text() != self._baseline_text
        except ValueError:
            modified = True  # Invalid intermediate input still belongs to the draft.
        self._set_modified(modified)
        for grid in self.findChildren(CanonFieldsGrid):
            grid._schedule()
        self._outline_timer.start()
        self.state_changed.emit()

    def _editor_menu(self, editor, point):
        menu = self._make_editor_menu(editor)
        menu.exec(editor.mapToGlobal(point))
        menu.deleteLater()

    def _make_editor_menu(self, editor):
        self._last_editor = editor
        self.history.update_view(self._history_view(editor))
        self.history.break_merge()
        # A structural undo may destroy the input that opened this menu.
        menu = QMenu(self)
        for label, command, available in (("撤销\tCtrl+Z", "undo", self.history.can_undo),
                                           ("重做\tCtrl+Y", "redo", self.history.can_redo)):
            action = menu.addAction(label, lambda _=False, c=command: self.edit_command(c))
            action.setEnabled(available)
        menu.addSeparator()
        selected = (bool(editor.selectedText()) if isinstance(editor, QLineEdit)
                    else editor.textCursor().hasSelection())
        for label, command in (("剪切", "cut"), ("复制", "copy"), ("粘贴", "paste")):
            action = menu.addAction(label, getattr(editor, command))
            action.setEnabled(selected if command in {"cut", "copy"} else
                              bool(QApplication.clipboard().text()))
        delete = menu.addAction("删除", editor.del_ if isinstance(editor, QLineEdit)
                                else lambda: editor.textCursor().removeSelectedText())
        delete.setEnabled(selected)
        menu.addSeparator()
        menu.addAction("全选", editor.selectAll)
        return menu

    def _history_step(self, direction):
        self.history.update_view(self._history_view())
        previous = self.history.current
        snapshot = self.history.step(direction)
        if snapshot is None:
            return
        self._view_restore_timer.stop()
        self._pending_view_restore = None
        self._history_typing = None
        self._history_restoring = True
        try:
            bindings = {(s.module_id if s else None, k): e for s, k, e in self._editor_bindings()}
            if not ordered_equal(self.entry, snapshot.entry) or bindings.keys() != snapshot.values.keys():
                self.entry = deepcopy(snapshot.entry)
                self._build_form()
                # Relationship rows are live controls rather than schema fields;
                # snapshots also preserve empty rows and deletion of the last row.
                for section in self._relation_sections:
                    body = self._module_bodies[section.module_id]
                    for item, editor in list(self._relation_rows):
                        if item is section:
                            row = editor.parentWidget()
                            row.setParent(None)
                            row.deleteLater()
                    self._relation_rows = [(s, e) for s, e in self._relation_rows if s is not section]
                    for (identity, key), text in snapshot.values.items():
                        if identity == section.module_id and key[0] == "relation":
                            self._add_relation_row(section, body.layout(), text)
                bindings = {(s.module_id if s else None, k): e for s, k, e in self._editor_bindings()}
            for key, text in snapshot.values.items():
                editor = bindings[key]
                editor.blockSignals(True)
                if isinstance(editor, QLineEdit):
                    if editor.text() != text:
                        editor.setText(text)
                else:
                    if editor.toPlainText() != text:
                        editor.setPlainText(text)
                        editor._schedule_fit()
                editor.blockSignals(False)
            self.feedback.setText(self.save_error)
            self._sync_history_dirty()
            # Restore the editor/selection; a structural deletion may have no
            # surviving focus, in which case focus the changed module's heading.
            target = snapshot.view.get("focus")
            if target is None:
                changed = [key for key, value in snapshot.values.items()
                           if key in bindings and previous.values.get(key) != value]
                if len(changed) == 1:
                    identity, key = changed[0]
                    target = (identity, key, 0, 0)
            restored_ids = set(s.module_id for s in snapshot.entry.sections) - set(
                s.module_id for s in previous.entry.sections)

            def restore_view():
                if self.history.current is not snapshot:
                    return
                for identity in restored_ids:
                    self._module_toggles[identity].setChecked(True)
                editor = bindings.get((target[0], target[1])) if target else None
                if editor is not None:
                    self._last_editor = editor
                    self._reveal_editor(editor)
                    editor.setFocus()
                    position, anchor = target[2:]
                    if isinstance(editor, QLineEdit):
                        editor.setSelection(anchor, position - anchor)
                    else:
                        cursor = editor.textCursor()
                        limit = editor.document().characterCount() - 1
                        cursor.setPosition(min(anchor, limit))
                        cursor.setPosition(min(position, limit), cursor.MoveMode.KeepAnchor)
                        editor.setTextCursor(cursor)
                else:
                    self.form_scroll.verticalScrollBar().setValue(snapshot.view["scroll"])
                for identity in restored_ids:
                    self.reveal_module(identity)
                    if target is None or target[0] != identity:
                        self._module_toggles[identity].setFocus()

            restore_view()
            self._pending_view_restore = restore_view
            self._view_restore_timer.start(0)
        finally:
            self._history_restoring = False
        self.outline_changed.emit()
        self.history_changed.emit()

    def _track_current_module(self, _value: int = 0) -> None:
        """按滚动位置推导视口顶部所在的分区，供分区大纲高亮。

        显式跳转优先于滚动推导，避免页底无法贴顶时选中前一个分区。
        """
        if self._navigation_target is not None:
            self._set_current_module(self._navigation_target)
            return
        content = self.form_scroll.widget()
        if content is None:
            return
        viewport_top = self.form_scroll.verticalScrollBar().value()
        current, best = "", -1
        for module_id, toggle in self._module_toggles.items():
            top = toggle.parentWidget().mapTo(content, QPoint(0, 0)).y()
            if top <= viewport_top + 12 and top > best:
                best, current = top, module_id
        self._set_current_module(current)

    def _set_current_module(self, module_id: str) -> None:
        if module_id != self._current_module:
            self._current_module = module_id
            self.current_module_changed.emit(module_id)

    def _set_modified(self, value: bool) -> None:
        value = bool(value)
        if value != self._modified:
            self._modified = value
            self.modified_changed.emit(value)

    def _mark_modified(self) -> None:
        self._set_modified(True)
        self._outline_timer.start()

    def _load_alias_map(self) -> dict[str, str]:
        """从人物身份记录构建 别名 → 主名 映射；记录损坏时降级为空映射。"""
        try:
            registry = load_registry(self.project)
        except (ValueError, OSError):
            return {}
        return {name: group["primary"] for group in registry["groups"]
                for name in group["names"] if name != group["primary"]}

    # ---------- 表单构建 ----------

    def _schedule_visual_layout(self):
        self._layout_timer.start()

    def _settle_visual_layout(self):
        focus = QApplication.focusWidget()
        if focus in self._editable_widgets() and focus.isVisibleTo(self.form_scroll.widget()):
            self.form_scroll.ensureWidgetVisible(focus, 16, 16)
        self.view_layout_changed.emit()

    def _build_form(self) -> None:
        self.form_about_to_rebuild.emit()
        self._history_building = True
        self._layout_timer.stop()
        self._intro_card = None
        self._search_labels.clear()
        self._navigation_target = None
        self._navigation_timer.stop()
        self._last_editor = None
        self._preamble_widgets.clear()
        self._suffix_widgets.clear()
        self._field_widgets.clear()
        self._group_widgets.clear()
        self._text_widgets.clear()
        self._raw_widgets.clear()
        self._extras_rows.clear()
        self._relation_rows.clear()
        self._relation_sections.clear()
        self._module_bodies.clear()
        self._module_toggles.clear()
        self._heading_toggles.clear()
        content = QWidget()
        content.setObjectName("canonFormContent")
        body = QVBoxLayout(content)
        body.setContentsMargins(6, 6, 6, 12)
        body.setSpacing(14)
        if self.entry is not None:
            controls = QHBoxLayout()
            controls.addStretch()
            for text, expanded in (("全部展开", True), ("全部折叠", False)):
                button = QPushButton(text)
                button.setObjectName("moduleOpButton")
                button.clicked.connect(lambda _=False, value=expanded: self.set_modules_expanded(value))
                controls.addWidget(button)
            body.addLayout(controls)
            body.addWidget(self._preamble_panel())
            for section in self.entry.sections:
                widget = self._section_widget(section)
                if widget is not None:
                    body.addWidget(widget)
                if section.suffix.strip():
                    body.addWidget(self._suffix_panel(section))
        body.addStretch()
        self.form_scroll.setWidget(content)
        for editor in self._editable_widgets():
            self._bind_history_editor(editor)
        self._history_building = False
        self._set_current_module("")
        self.outline_changed.emit()
        self.form_rebuilt.emit()

    def _preamble_panel(self) -> QWidget:
        box = CanonIntroCard()
        self._intro_card = box
        box.visibility_changed.connect(self._schedule_visual_layout)
        blocks = preamble_blocks(self.entry)
        visible = [i for i, (_start, _end, text) in enumerate(blocks) if text.strip()]
        if not visible:
            visible = [len(blocks) - 1]
        for index in visible:
            editor = CanonTextEdit(blocks[index][2], maximum_lines=6)
            editor.setAccessibleName("资料导语")
            editor.setPlaceholderText("资料概述，可留空。")
            editor.textChanged.connect(self._mark_modified)
            box.add_editor(editor)
            self._preamble_widgets.append((index, editor))
        return box

    def _suffix_panel(self, section: CanonSection) -> QWidget:
        box = CanonCard("canonIntroCard")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(10)
        label = QLabel("区块间备注")
        label.setObjectName("canonModuleTitle")
        layout.addWidget(label)
        editor = CanonTextEdit(section.suffix.replace("\r\n", "\n").replace("\r", "\n").strip("\n"), maximum_lines=6)
        editor.setAccessibleName("区块间备注")
        editor.textChanged.connect(self._mark_modified)
        layout.addWidget(editor)
        self._suffix_widgets.append((section, editor))
        return box

    def _section_widget(self, section: CanonSection):
        if section.role == ROLE_MANAGED:
            return self._managed_widget(section)
        body = self._section_body(section)
        if body is None:
            return None
        return self._wrap_module(section, body)

    def _collapsed_modules(self) -> set[str]:
        key = str(self.path.resolve()) if self.path else ""
        if key not in self._collapse_by_path:
            seeded = None
            if self.path is not None and self.collapse_state_loader is not None:
                seeded = self.collapse_state_loader(self.path)
            self._collapse_by_path[key] = set(seeded) if seeded else set()
        return self._collapse_by_path[key]

    def set_modules_expanded(self, expanded: bool) -> None:
        for identity in self._module_toggles:
            self._set_module_expanded(identity, expanded)

    def _set_module_expanded(self, identity, expanded, *, persist=True):
        body, toggle = self._module_bodies[identity], self._module_toggles[identity]
        focus = QApplication.focusWidget()
        if not expanded and (body is focus or body.isAncestorOf(focus)):
            toggle.setFocus()
        blocked = toggle.blockSignals(True)
        toggle.setChecked(expanded)
        toggle.blockSignals(blocked)
        body.setVisible(expanded)
        toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        section = next(s for s in self.entry.sections if s.module_id == identity)
        action = "折叠" if expanded else "展开"
        toggle.setAccessibleName(f"{action}模块：{section.heading}")
        toggle.setToolTip(f"{action}模块（不影响保存）")
        if not persist:
            return
        self._search_expanded.discard(identity)
        collapsed = self._collapsed_modules()
        changed = (identity in collapsed) == expanded
        collapsed.discard(identity) if expanded else collapsed.add(identity)
        if changed and self.collapse_state_saver is not None and self.path is not None:
            self.collapse_state_saver(self.path, set(collapsed) & set(self._module_bodies))

    def reveal_search_module(self, identity):
        if identity and identity in self._module_toggles:
            if not self._module_toggles[identity].isChecked():
                self._search_expanded.add(identity)
                self._set_module_expanded(identity, True, persist=False)

    def restore_search_expansion(self):
        for identity in list(self._search_expanded):
            if identity in self._module_toggles and identity in self._collapsed_modules():
                self._set_module_expanded(identity, False, persist=False)
        self._search_expanded.clear()

    def _wrap_module(self, section: CanonSection, body: QWidget) -> QFrame:
        box = CanonCard()
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(16, 12, 16, 16)
        vbox.setSpacing(12)
        heading = QWidget()
        header = QHBoxLayout(heading)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        toggle = QToolButton()
        toggle.setObjectName("canonModuleToggle")
        toggle.setCheckable(True)
        toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        toggle.setProperty("moduleId", section.module_id)
        header.addWidget(toggle)
        title = WrappingLabel(section.heading or "未识别章节")
        title.setObjectName("canonModuleTitle")
        self._search_labels[(section.module_id, ("heading",))] = title
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        title.setMinimumWidth(0)
        policy = title.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
        title.setSizePolicy(policy)
        header.addWidget(title, 1, Qt.AlignmentFlag.AlignVCenter)
        if section.role in (ROLE_RAW, ROLE_FREE):
            badge = QLabel("原文区块")
            badge.setObjectName("rawModuleBadge")
            header.addWidget(badge)
        if is_system_section(self.entry, section):
            badge = QLabel("自动同步·只读" if section.role == ROLE_MANAGED else "结构固定")
            badge.setObjectName("systemModuleBadge")
            badge.setToolTip("通过同步预览确认更新，内容只读。" if section.role == ROLE_MANAGED
                             else "模块名称、位置和预设字段固定，字段内容可以编辑。")
            header.addWidget(badge)
        operations = None
        if not is_system_section(self.entry, section):
            operations = self._module_menu(section)
        vbox.addWidget(CanonModuleHeader(heading, operations))
        vbox.addWidget(body)
        self._module_bodies[section.module_id] = body
        self._module_toggles[section.module_id] = toggle
        self._heading_toggles[heading] = toggle
        heading.setCursor(Qt.CursorShape.PointingHandCursor)
        heading.setToolTip("点击标题行展开或折叠该模块")
        heading.installEventFilter(self)

        toggle.toggled.connect(lambda expanded, identity=section.module_id:
                               self._set_module_expanded(identity, expanded))
        expanded = (section.module_id not in self._collapsed_modules()
                    or section.module_id in self._search_expanded)
        self._set_module_expanded(section.module_id, expanded, persist=False)
        return box

    def _module_menu(self, section: CanonSection) -> QToolButton:
        """模块的结构操作收敛为一个 ⋯ 菜单，避免每行常驻四个按钮。"""
        button = QToolButton()
        button.setObjectName("moduleMenuButton")
        set_button_icon(button, "more_horiz",
                        str(colors_for(self._theme_config).get("muted_text_color", "#63748A")),
                        size=18)
        button.setAccessibleName(f"模块操作：{section.heading}")
        button.setToolTip(f"模块操作：{section.heading}")
        button.setProperty("moduleHeading", section.heading)
        button.setProperty("moduleId", section.module_id)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        for text, name, handler in (
            ("重命名模块…", "moduleOpAction", lambda _=False, s=section: self._rename_module(s)),
            ("上移", "moduleOpAction", lambda _=False, s=section: self._move_module(s, -1)),
            ("下移", "moduleOpAction", lambda _=False, s=section: self._move_module(s, 1)),
            ("删除模块…", "moduleDeleteAction", lambda _=False, s=section: self._delete_module(s)),
        ):
            action = menu.addAction(text)
            action.setObjectName(name)
            action.setProperty("moduleHeading", section.heading)
            action.setProperty("moduleId", section.module_id)
            action.triggered.connect(handler)
        button.setMenu(menu)
        return button

    def _section_body(self, section: CanonSection):
        if section.role == ROLE_FIELDS:
            body = QWidget()
            self._fields_form(body, section)
            return body
        if section.role == ROLE_GROUP:
            body = QWidget()
            vbox = QVBoxLayout(body)
            vbox.setContentsMargins(0, 0, 0, 0)
            vbox.setSpacing(10)
            if section.extras:
                vbox.addWidget(self._extras_panel(section, None))
            for group in section.groups:
                group_box = QFrame()
                group_box.setObjectName("canonGroupFrame")
                layout = QVBoxLayout(group_box)
                layout.setContentsMargins(12, 10, 12, 12)
                layout.setSpacing(8)
                row = QHBoxLayout()
                title = WrappingLabel(group.name)
                self._search_labels[(section.module_id, ("group-label", group.name))] = title
                title.setObjectName("canonModuleTitle")
                title.setTextFormat(Qt.TextFormat.PlainText)
                title.setWordWrap(True)
                title.setMinimumWidth(0)
                policy = title.sizePolicy()
                policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
                title.setSizePolicy(policy)
                row.addWidget(title, 1, Qt.AlignmentFlag.AlignVCenter)
                row.addWidget(self._group_menu(section, group.name))
                layout.addLayout(row)
                fields = QWidget()
                self._fields_form(fields, section, group.name)
                layout.addWidget(fields)
                vbox.addWidget(group_box)
            add_button = QPushButton("＋ 添加条目")
            add_button.setProperty("moduleHeading", section.heading)
            add_button.clicked.connect(lambda _=False, s=section: self._add_group(s))
            vbox.addWidget(add_button, alignment=Qt.AlignmentFlag.AlignLeft)
            return body
        if section.role == ROLE_TEXT:
            if (self.project is not None and self.kind == "character"
                    and section.schema_key == "关键关系"):
                return self._relation_body(section)
            editor = CanonTextEdit(section.text)
            editor.setAccessibleName(section.heading)
            editor.setPlaceholderText(section_hint(section.heading, section.role))
            editor.textChanged.connect(lambda: self._mark_modified())
            self._text_widgets.append((section, editor))
            return editor
        # 自由备注 / 未识别章节：原文区块，正文整块编辑、逐字写回
        body = QWidget()
        vbox = QVBoxLayout(body)
        vbox.setContentsMargins(0, 0, 0, 0)
        editor = CanonTextEdit(raw_section_body(section))
        editor.setAccessibleName(section.heading)
        editor.setPlaceholderText(section_hint(section.heading, section.role))
        editor.textChanged.connect(lambda: self._mark_modified())
        self._raw_widgets.append((section, editor))
        vbox.addWidget(editor)
        caption = QLabel(section.warning or "该区块保留原文格式，修改后原样写入文档。")
        caption.setObjectName("canonCaption")
        caption.setWordWrap(True)
        vbox.addWidget(caption)
        return body

    def _extras_panel(self, section: CanonSection, group_name: str | None) -> QWidget:
        """模块内手写行的可编辑行组：每行一个输入框，保存时逐字写回。"""
        lines = (section.extras if group_name is None
                 else next(g.extras for g in section.groups if g.name == group_name))
        panel = QWidget()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(4)
        caption = QLabel("以下为手写内容，保存时逐字保留：")
        caption.setObjectName("canonCaption")
        caption.setWordWrap(True)
        vbox.addWidget(caption)
        rows: list[QLineEdit] = []
        for text in lines:
            editor = QLineEdit(text)
            editor.textChanged.connect(lambda _text: self._mark_modified())
            rows.append(editor)
            vbox.addWidget(editor)
        self._extras_rows.append((section, group_name, rows))
        return panel

    def _managed_widget(self, section: CanonSection) -> QFrame:
        body = CanonSyncView(raw_section_body(section), self.project)
        source = str(self.path)
        body.sync_requested.connect(lambda target=source: self.sync_requested.emit(target))
        body.chapter_requested.connect(lambda chapter_id, target=source:
                                      self.chapter_requested.emit(target, chapter_id))
        body.set_sync_available(self._sync_available, self._sync_unavailable_reason)
        return self._wrap_module(section, body)

    def set_sync_available(self, available: bool, reason: str = "") -> None:
        self._sync_available = available
        self._sync_unavailable_reason = reason
        for view in self.findChildren(CanonSyncView):
            view.set_sync_available(available, reason)

    def _fields_form(self, body, section, group_name=None):
        form = QVBoxLayout(body)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        grid = None
        if self.kind == "character" and section.schema_key == "基础档案" and group_name is None:
            grid = CanonFieldsGrid()
            grid.reflowed.connect(self._schedule_visual_layout)
            form.addWidget(grid)
        group = next((g for g in section.groups if g.name == group_name), None)
        fields = section.fields if group_name is None else group.fields
        extras = section.extras if group_name is None else group.extras
        declared = list((section_spec(self.kind, section) or {}).get("keys", []))
        for key in [*declared, *(k for k in fields if k not in declared)]:
            label = WrappingLabel(key)
            self._search_labels[(section.module_id, ("field-label", group_name, key))] = label
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setAccessibleName(key)
            label.setWordWrap(True)
            policy = label.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            label.setSizePolicy(policy)
            label.setObjectName("canonFieldLabel")
            field = QWidget()
            column = QVBoxLayout(field)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            column.addWidget(label)
            if grid is None:
                form.addWidget(field)
            editor = QLineEdit(fields.get(key, ""))
            editor.setAccessibleName(f"{group_name + ' / ' if group_name else ''}{key}")
            editor.setPlaceholderText(FIELD_HINTS.get(key, ""))
            label.setBuddy(editor)
            editor.textChanged.connect(lambda _text: self._mark_modified())
            if group_name is None:
                self._field_widgets.append((section, key, editor))
            else:
                self._group_widgets.append((section, group_name, key, editor))
            if key in declared:
                editor.setToolTip("模板预设字段：可编辑内容，名称与位置受保护。")
                column.addWidget(editor)
            else:
                row = QWidget()
                layout = QHBoxLayout(row)
                layout.setContentsMargins(0, 0, 0, 0)
                layout.addWidget(editor, 1)
                layout.addWidget(self._field_menu(section, key, group_name))
                column.addWidget(row)
            if grid is not None:
                grid.add_field(field, label, editor,
                    short=key in {"姓名", "别名", "年龄／出生信息", "性别", "首次出场"})
        if extras:
            form.addWidget(self._extras_panel(section, group_name))
        add = QPushButton("＋ 添加字段")
        add.setProperty("moduleId", section.module_id)
        add.setProperty("groupName", group_name)
        add.clicked.connect(lambda _=False: self._add_field(section, group_name))
        form.addWidget(add, alignment=Qt.AlignmentFlag.AlignLeft)

    def _management_menu(self, label, section, group=None, key=None):
        button = QToolButton()
        button.setText(label)
        button.setObjectName("canonManageButton")
        button.setAccessibleName(f"{label}：{group or section.heading}{' / ' + key if key else ''}")
        button.setToolTip(button.accessibleName())
        button.setProperty("moduleId", section.module_id)
        button.setProperty("groupName", group)
        button.setProperty("fieldName", key)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(button)
        button.setMenu(menu)
        return button, menu

    def _field_menu(self, section, key, group):
        button, menu = self._management_menu("更多", section, group, key)
        menu.addAction("重命名字段…", lambda: self._rename_field(section, key, group))
        fields = section.fields if group is None else next(g.fields for g in section.groups if g.name == group)
        declared = (section_spec(self.kind, section) or {}).get("keys", [])
        keys = [k for k in fields if k not in declared]
        for label, offset in (("上移字段", -1), ("下移字段", 1)):
            action = menu.addAction(label, lambda _checked=False, step=offset: self._perform_structure(
                lambda draft: move_field(draft, section.module_id, key, step, group)))
            action.setEnabled(0 <= keys.index(key) + offset < len(keys))
        menu.addSeparator()
        menu.addAction("删除字段…", lambda: self._delete_field(section, key, group))
        return button

    def _group_menu(self, section, name):
        button, menu = self._management_menu("管理条目", section, name)
        menu.addAction("重命名条目…", lambda: self._rename_group(section, name))
        index = next(i for i, g in enumerate(section.groups) if g.name == name)
        for label, offset in (("上移条目", -1), ("下移条目", 1)):
            action = menu.addAction(label, lambda _checked=False, step=offset: self._perform_structure(
                lambda draft: move_group(draft, section.module_id, name, step)))
            action.setEnabled(0 <= index + offset < len(section.groups))
        menu.addSeparator()
        menu.addAction("删除条目…", lambda: self._delete_group(section, name))
        return button

    def _perform_structure(self, operation, remap_focus=None):
        try:
            self._change_structure(operation, remap_focus=remap_focus)
        except ValueError as exc:
            self.feedback.setText(str(exc))

    def _add_field(self, section, group=None):
        name, ok = QInputDialog.getText(self, "添加字段", "字段名称：")
        if ok:
            self._perform_structure(lambda draft: add_field(draft, section.module_id, name, group))

    def _rename_field(self, section, key, group=None):
        name, ok = QInputDialog.getText(self, "重命名字段", "新名称：", text=key)
        if not ok:
            return
        old_key = ("field", key) if group is None else ("group", group, key)
        new_key = ("field", name.strip()) if group is None else ("group", group, name.strip())
        self._perform_structure(lambda draft: rename_field(draft, section.module_id, key, name, group),
            lambda item, value: new_key if item is not None and item.module_id == section.module_id and value == old_key else value)

    def _delete_field(self, section, key, group=None):
        if self._confirm_remove("删除字段", f"删除字段「{key}」及其内容？保存后生效。"):
            self._perform_structure(lambda draft: delete_field(draft, section.module_id, key, group))

    def _rename_group(self, section, name):
        new, ok = QInputDialog.getText(self, "重命名条目", "新名称：", text=name)
        if not ok:
            return
        def remap(item, key):
            if item is not None and item.module_id == section.module_id and key[0] in {"group", "extras"} and key[1] == name:
                return (key[0], new.strip(), *key[2:])
            return key
        self._perform_structure(lambda draft: rename_group(draft, section.module_id, name, new), remap)

    def _delete_group(self, section, name):
        if self._confirm_remove("删除条目", f"删除条目「{name}」及其全部字段和手写内容？保存后生效。"):
            self._perform_structure(lambda draft: delete_group(draft, section.module_id, name))

    def _confirm_remove(self, title, text):
        return QMessageBox.question(self, title, text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    # ---------- 关键关系选择器（角色卡 + 传入 project 时） ----------

    def _relation_body(self, section: CanonSection) -> QWidget:
        """关键关系分区主体（标题与模块操作按钮由 _wrap_module 提供）。"""
        self._relation_sections.append(section)
        body = QWidget()
        vbox = QVBoxLayout(body)
        vbox.setContentsMargins(0, 0, 0, 0)
        for line in section.text.split("\n"):
            if line.strip():
                self._add_relation_row(section, vbox, line)
        add_button = QPushButton("＋ 关联角色")
        add_button.clicked.connect(lambda _=False, s=section, v=vbox: self._pick_relation(s, v))
        vbox.addWidget(add_button, alignment=Qt.AlignmentFlag.AlignLeft)
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
        last = vbox.itemAt(vbox.count() - 1) if vbox.count() else None
        if last is not None and isinstance(last.widget(), QPushButton):
            vbox.insertWidget(vbox.count() - 1, row)
        else:
            vbox.addWidget(row)
        self._relation_rows.append((section, editor))
        if not self._history_building:
            self._bind_history_editor(editor)

    def _remove_relation_row(self, editor: QLineEdit, row: QWidget) -> None:
        self.history.update_view(self._history_view(editor))
        self._relation_rows = [(s, e) for s, e in self._relation_rows if e is not editor]
        row.setParent(None)
        row.deleteLater()
        self._mark_modified()
        self._record_history()

    def _pick_relation(self, section: CanonSection, vbox: QVBoxLayout) -> None:
        candidates = self._relation_candidates()
        if not candidates:
            self.feedback.setText("项目内暂无其他角色可供关联。")
            return
        selected, ok = QInputDialog.getItem(self, "关联角色", "选择角色：", candidates, 0, False)
        if not ok or not selected:
            return
        self.history.update_view(self._history_view())
        self._add_relation_row(section, vbox, f"- {selected}：")
        self._mark_modified()
        self._record_history()
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
        if ok:
            self._perform_structure(lambda draft: add_group(draft, section.module_id, name))

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
            self._change_structure(lambda draft: rename_module(draft, section.module_id, text))
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return


    def _move_module(self, section: CanonSection, offset: int) -> None:
        try:
            self._change_structure(lambda draft: move_module(draft, section.module_id, offset))
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return


    def _delete_module(self, section: CanonSection) -> None:
        answer = QMessageBox.question(
            self, "删除模块",
            f"删除模块「{section.heading}」？其内容将在保存时从 md 文档中移除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._change_structure(lambda draft: delete_module(draft, section.module_id))
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return


    # ---------- 保存 ----------

    def _collected_entry(self) -> CanonEntry:
        draft = deepcopy(self.entry)
        targets = {id(original): CanonEntry(kind=draft.kind, title="", preamble="", sections=[copy])
                   for original, copy in zip(self.entry.sections, draft.sections)}
        for index, editor in reversed(self._preamble_widgets):
            set_preamble_text(draft, index, editor.toPlainText())
        for section, editor in self._suffix_widgets:
            set_section_suffix(targets[id(section)], section.module_id, editor.toPlainText())
        for section, key, editor in self._field_widgets:
            if editor.text() != section.fields.get(key, ""):
                set_section_field(targets[id(section)], section.module_id, key, editor.text())
        for section, group_name, key, editor in self._group_widgets:
            group = next(g for g in section.groups if g.name == group_name)
            if editor.text() != group.fields.get(key, ""):
                set_group_field(targets[id(section)], section.module_id, group_name, key, editor.text())
        for section, editor in self._text_widgets:
            if editor.toPlainText().strip("\n") != section.text:
                set_section_text(targets[id(section)], section.module_id, editor.toPlainText())
        # 原文区块：正文整块逐字写回（未修改的区块保持原字节）
        for section, editor in self._raw_widgets:
            if editor.toPlainText() != raw_section_body(section):
                set_raw_section_text(targets[id(section)], section.module_id, editor.toPlainText())
        # 模块内手写行：按行逐字写回
        for section, group_name, rows in self._extras_rows:
            if group_name is None:
                current = section.extras
            else:
                current = next(g.extras for g in section.groups if g.name == group_name)
            lines = [editor.text() for editor in rows]
            if lines != current:
                if group_name is None:
                    set_section_extras(targets[id(section)], section.module_id, lines)
                else:
                    set_group_extras(targets[id(section)], section.module_id, group_name, lines)
        # 关键关系选择器：按行拼接后写回
        for section in self._relation_sections:
            lines = [e.text() for s, e in self._relation_rows if s is section]
            original = [line for line in section.text.split("\n") if line.strip()]
            if lines != original:
                set_section_text(targets[id(section)], section.module_id, "\n".join(lines))

        return draft

    def _change_structure(self, operation, remap_focus=None) -> None:
        draft = self._collected_entry()
        before = render_canon_entry(draft)
        self.history.update_view(self._history_view())
        self.history.break_merge()
        # Remember the field before a dialog/menu took focus. Track the copied
        # section object so renames and moves do not invalidate its identity.
        focus = self._last_editor or QApplication.focusWidget()
        focused = None
        old_sections = list(self.entry.sections)
        for section, key, editor in self._editor_bindings():
            if editor is focus:
                index = next((i for i, item in enumerate(old_sections) if item is section), None)
                if isinstance(editor, QLineEdit):
                    cursor = (editor.cursorPosition(), editor.selectionStart(),
                              len(editor.selectedText().encode("utf-16-le")) // 2)
                else:
                    cursor = (editor.textCursor().position(), editor.textCursor().anchor())
                focused = (draft.sections[index] if index is not None else None, key, cursor)
                break
        scroll = self.form_scroll.verticalScrollBar().value()
        operation(draft)
        if render_canon_entry(draft) == before:
            return  # No-op dialogs do not consume a history step or rebuild inputs.
        if focused is not None and remap_focus is not None:
            section, key, cursor = focused
            focused = (section, remap_focus(section, key), cursor)
        self.entry = draft
        self._build_form()
        self.feedback.setText(self.save_error)
        self._mark_modified()

        def restore_view():
            if self.entry is not draft:
                return
            if focused is not None:
                section, key, cursor = focused
                for item, item_key, editor in self._editor_bindings():
                    if item is section and item_key == key:
                        self._last_editor = editor
                        if editor.isVisibleTo(self.form_scroll.widget()):
                            editor.setFocus()
                        if isinstance(editor, QLineEdit):
                            if cursor[1] >= 0:
                                editor.setSelection(cursor[1], cursor[2])
                            else:
                                editor.setCursorPosition(cursor[0])
                        else:
                            text_cursor = editor.textCursor()
                            text_cursor.setPosition(cursor[1])
                            text_cursor.setPosition(cursor[0], text_cursor.MoveMode.KeepAnchor)
                            editor.setTextCursor(text_cursor)
                        break
            self.form_scroll.verticalScrollBar().setValue(scroll)

        restore_view()
        self._pending_view_restore = restore_view
        self._view_restore_timer.start(0)
        self._record_history()

    def _effective_text(self) -> str:
        return render_canon_entry(self._collected_entry())
