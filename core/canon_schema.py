"""设定材料的字段 Schema 与受管区块序列化器。

设计约束（阶段 1，纯新增）：
- md 文件始终是单一事实源；本模块只提供"解析 ⇄ 渲染"映射，不改变任何现有消费者。
- 未修改的区块渲染时逐字节还原（round-trip 保证），只有被编辑的分区才会被
  重排并以 novalist:section:v1 受管标记包裹（与 auto-state 同款标记风格）。
- 解析容错：无法识别的行、手写内容、未知章节一律原样保留（进入 extras / raw），
  由上层以可编辑的“原文区块”或手写行形式逐字写回，绝不丢弃内容。
- "当前剧情状态（DeepSonder 同步）"为只读同步区块，"作者自由备注"永不结构化，
  二者只做逐字节透传。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from .project_data import CANON_ENTRY_TYPES
from .storage import atomic_write_text

SECTION_START = "<!-- novalist:section:v1:start -->"
SECTION_END = "<!-- novalist:section:v1:end -->"

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_SUB_RE = re.compile(r"^###\s+(.+?)\s*$")
_ITEM_RE = re.compile(r"^-\s+([^：:]+?)\s*[：:]\s*(.*)$")

ROLE_FIELDS = "fields"
ROLE_TEXT = "text"
ROLE_GROUP = "group"
ROLE_MANAGED = "managed"
ROLE_FREE = "free"
ROLE_RAW = "raw"

# 每类设定材料的分区 Schema。heading 必须与 project_data.CANON_ENTRY_TYPES
# 模板中的章节标题完全一致；未列出的标题按内容自动识别为用户模块
# （detect_module_role），识别不了的按 ROLE_RAW 原样保留。
# "fixed": True 标记系统区块：结构（标题、位置、去留）受保护，内容仍可编辑。
FIELD_SCHEMAS: dict[str, dict] = {
    "character": {
        "sections": [
            {"heading": "基础档案", "role": ROLE_FIELDS, "fixed": True, "keys": [
                "姓名", "别名", "年龄／出生信息", "性别", "身份／阵营", "首次出场"]},
            {"heading": "外貌与辨识特征", "role": ROLE_TEXT},
            {"heading": "性格、动机与核心欲望", "role": ROLE_TEXT},
            {"heading": "长期目标与人物弧线", "role": ROLE_TEXT},
            {"heading": "能力档案", "role": ROLE_GROUP, "keys": [
                "所属体系", "能力属性", "当前等级", "当前数值", "使用条件", "限制与代价", "克制关系"]},
            {"heading": "关键关系", "role": ROLE_TEXT},
            {"heading": "秘密与人物弧线", "role": ROLE_TEXT},
            {"heading": "当前剧情状态（DeepSonder 同步）", "role": ROLE_MANAGED},
            {"heading": "作者自由备注", "role": ROLE_FREE},
        ],
    },
    "world": {
        "sections": [
            {"heading": "核心规则", "role": ROLE_TEXT},
            {"heading": "地理与空间", "role": ROLE_TEXT},
            {"heading": "历史与现状", "role": ROLE_TEXT},
            {"heading": "社会、势力与秩序", "role": ROLE_TEXT},
            {"heading": "技术、魔法或特殊现象", "role": ROLE_TEXT},
            {"heading": "对剧情的约束", "role": ROLE_TEXT},
            {"heading": "已知例外与未解问题", "role": ROLE_TEXT},
        ],
    },
    "power": {
        "sections": [
            {"heading": "体系定位", "role": ROLE_TEXT},
            {"heading": "等级或层级", "role": ROLE_TEXT},
            {"heading": "核心原则", "role": ROLE_TEXT},
            {"heading": "获取方式", "role": ROLE_TEXT},
            {"heading": "使用限制", "role": ROLE_TEXT},
            {"heading": "代价与副作用", "role": ROLE_TEXT},
            {"heading": "克制关系", "role": ROLE_TEXT},
            {"heading": "对剧情的约束", "role": ROLE_TEXT},
            {"heading": "已知例外", "role": ROLE_TEXT},
        ],
    },
}


def schema_for(kind: str) -> dict:
    normalized = str(kind or "").strip().lower()
    if normalized not in FIELD_SCHEMAS or normalized not in CANON_ENTRY_TYPES:
        raise ValueError(f"未知的设定材料类型：{kind}")
    return FIELD_SCHEMAS[normalized]


def section_spec(kind: str, heading: str) -> dict | None:
    """返回某分区声明的角色与字段键（供表单渲染缺失字段）。"""
    for spec in schema_for(kind)["sections"]:
        if spec["heading"] == heading:
            return spec
    return None


def kind_for_path(path: Path | str) -> str | None:
    """按 canon 目录结构（characters/world/power）推导材料类型；无法推导返回 None。"""
    parent = Path(path).parent.name
    for kind, spec in CANON_ENTRY_TYPES.items():
        if parent == spec["directory"] and kind in FIELD_SCHEMAS:
            return kind
    return None


def detect_module_role(heading: str, raw_body: str) -> str:
    """对 schema 未声明的小节按内容猜测用户模块类型（旧文件兼容）。

    猜测规则（保证每种猜测都能被 _render_dirty 无损写回）：
    - ### 子分组开头且其后含 - 键：值 列表项 → 分组型；
    - 含 - 键：值 列表项且无子分组 → 字段型（混杂的手写行进 extras 保留）；
    - 其余普通文本（含只有子分组标题的骨架）→ 文本型；
    - 表格、代码块、含受管标记的内容，以及顶层字段与子分组混排的结构
      无法按单一角色无损渲染，一律保持 ROLE_RAW 原样只读。
    未编辑的模块渲染时逐字节还原，类型猜测不影响 round-trip。
    """
    lines = [line.strip() for line in str(raw_body).splitlines()]
    body = [line for line in lines if line]
    if not body:
        return ROLE_TEXT
    if any(line.startswith(("|", "```")) or "<!--" in line for line in body):
        return ROLE_RAW
    has_sub = any(_SUB_RE.match(line) for line in body)
    has_item = any(_ITEM_RE.match(line) for line in body)
    if has_sub and has_item and _SUB_RE.match(body[0]):
        return ROLE_GROUP
    if has_item and not has_sub:
        return ROLE_FIELDS
    if not has_item:
        return ROLE_TEXT
    return ROLE_RAW


@dataclass
class CanonGroup:
    """能力档案等可重复分组中的一个条目（对应一个 ### 小节）。"""

    name: str
    fields: dict[str, str] = dataclass_field(default_factory=dict)
    extras: list[str] = dataclass_field(default_factory=list)


@dataclass
class CanonSection:
    heading: str
    role: str
    raw: str                 # 原始字节跨度（含标题行、区块间空行），未编辑时逐字节还原
    marked: bool = False     # 原文中是否已带 section 受管标记
    fields: dict[str, str] = dataclass_field(default_factory=dict)
    extras: list[str] = dataclass_field(default_factory=list)
    text: str = ""
    groups: list[CanonGroup] = dataclass_field(default_factory=list)
    dirty: bool = False


@dataclass
class CanonEntry:
    kind: str
    title: str
    preamble: str           # 首个章节之前的全部原文（含 # 标题行与卡片标记注释）
    sections: list[CanonSection] = dataclass_field(default_factory=list)

    def section(self, heading: str) -> CanonSection | None:
        return next((s for s in self.sections if s.heading == heading), None)


def parse_canon_entry(kind: str, text: str) -> CanonEntry:
    """解析一份设定材料 md。解析永不抛内容异常，识别不了的部分原样保留。"""
    schema_for(kind)  # 仅校验类型
    lines = text.splitlines(keepends=True)
    n = len(lines)

    # 扫描各章节起点（受管标记或 ## 标题行），标记内不重复分段
    starts: list[tuple[int, bool]] = []
    i = 0
    while i < n:
        stripped = lines[i].rstrip("\r\n")
        if stripped == SECTION_START:
            end = next((k for k in range(i + 1, n) if lines[k].rstrip("\r\n") == SECTION_END), None)
            if end is not None:
                starts.append((i, True))
                i = end + 1
                continue
        match = _HEADING_RE.match(stripped)
        if match:
            starts.append((i, False))
        i += 1

    preamble = "".join(lines[: starts[0][0]]) if starts else text
    title = ""
    for line in preamble.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            title = match.group(1)
            break

    entry = CanonEntry(kind=kind, title=title, preamble=preamble)
    for idx, (start, marked) in enumerate(starts):
        stop = starts[idx + 1][0] if idx + 1 < len(starts) else n
        raw = "".join(lines[start:stop])
        content = _span_content(lines, start, stop, marked)
        heading_line = next((l for l in content if _HEADING_RE.match(l.rstrip("\r\n"))), None)
        if heading_line is None:
            entry.sections.append(CanonSection(heading="", role=ROLE_RAW, raw=raw, marked=marked))
            continue
        heading = _HEADING_RE.match(heading_line.rstrip("\r\n")).group(1)
        body = content[content.index(heading_line) + 1:]
        spec = section_spec(kind, heading)
        role = spec["role"] if spec is not None else detect_module_role(heading, "".join(body))
        section = CanonSection(heading=heading, role=role, raw=raw, marked=marked)
        if role in (ROLE_FIELDS, ROLE_GROUP):
            _parse_structured(section, body)
        elif role == ROLE_TEXT:
            section.text = "".join(body).replace("\r\n", "\n").strip("\n")
        entry.sections.append(section)
    return entry


def _span_content(lines: list[str], start: int, stop: int, marked: bool) -> list[str]:
    if marked:
        end = next(k for k in range(start + 1, stop) if lines[k].rstrip("\r\n") == SECTION_END)
        return lines[start + 1: end]
    return lines[start: stop]


def _parse_structured(section: CanonSection, body: list[str]) -> None:
    group: CanonGroup | None = None
    for line in body:
        stripped = line.rstrip("\r\n")
        if not stripped:
            continue
        sub = _SUB_RE.match(stripped)
        if sub is not None:
            group = CanonGroup(name=sub.group(1))
            section.groups.append(group)
            continue
        item = _ITEM_RE.match(stripped)
        value = (item.group(2) if item else "").strip()
        if item is not None and group is None:
            section.fields[item.group(1).strip()] = value
        elif item is not None:
            group.fields[item.group(1).strip()] = value
        elif group is None:
            section.extras.append(stripped)
        else:
            group.extras.append(stripped)


def render_canon_entry(entry: CanonEntry) -> str:
    """渲染回 md。未编辑的分区逐字节还原；编辑过的分区规范化并加受管标记。"""
    parts = [entry.preamble]
    for section in entry.sections:
        parts.append(section.raw if not section.dirty else _render_dirty(section))
    return "".join(parts)


def _render_dirty(section: CanonSection) -> str:
    trailing = len(section.raw) - len(section.raw.rstrip("\n"))
    lines: list[str] = []
    if section.role == ROLE_FIELDS:
        lines = [f"## {section.heading}", ""]
        lines += [f"- {key}：{value}" for key, value in section.fields.items()]
        lines += section.extras
    elif section.role == ROLE_GROUP:
        lines = [f"## {section.heading}", ""]
        lines += section.extras
        for group in section.groups:
            lines += [f"### {group.name}", ""]
            lines += [f"- {key}：{value}" for key, value in group.fields.items()]
            lines += group.extras
    elif section.role == ROLE_TEXT:
        lines = [f"## {section.heading}"]
        if section.text:
            lines += ["", section.text]
    else:
        raise ValueError(f"分区「{section.heading}」为只读或自由内容，不支持表单化写回。")
    wrapped = [SECTION_START, *lines, SECTION_END]
    return "\n".join(wrapped) + "\n" * max(trailing, 1)


def section_or_raise(entry: CanonEntry, heading: str) -> CanonSection:
    section = entry.section(heading)
    if section is None:
        raise ValueError(f"材料中不存在分区「{heading}」。")
    return section


def set_section_field(entry: CanonEntry, heading: str, key: str, value: str) -> None:
    section = section_or_raise(entry, heading)
    if section.role == ROLE_MANAGED:
        raise ValueError("该分区由 DeepSonder 自动同步，表单内只读。")
    if section.role != ROLE_FIELDS:
        raise ValueError(f"分区「{heading}」不是字段分区，无法以字段方式编辑。")
    section.fields[key] = str(value)
    section.dirty = True


def set_group_field(entry: CanonEntry, heading: str, name: str, key: str, value: str) -> None:
    section = section_or_raise(entry, heading)
    if section.role != ROLE_GROUP:
        raise ValueError(f"分区「{heading}」不是可重复分组分区。")
    group = next((g for g in section.groups if g.name == name), None)
    if group is None:
        group = CanonGroup(name=name)
        section.groups.append(group)
    group.fields[key] = str(value)
    section.dirty = True


def set_section_text(entry: CanonEntry, heading: str, text: str) -> None:
    section = section_or_raise(entry, heading)
    if section.role == ROLE_MANAGED:
        raise ValueError("该分区由 DeepSonder 自动同步，表单内只读。")
    if section.role != ROLE_TEXT:
        raise ValueError(f"分区「{heading}」不是文本分区，无法以文本方式编辑。")
    section.text = str(text).strip("\n")
    section.dirty = True


# ---------- 原文区块与手写行（Phase C）：md 完全后台化的逐字写回 ----------


def raw_section_body(section: CanonSection) -> str:
    """读取 RAW/FREE 原文区块去掉标题行后的正文（LF 归一，去首尾空行）。

    无标题的原文区块（历史文件中标记包裹却无 ## 标题的罕见残片）返回
    整段原文（含受管标记行），由 set_raw_section_text 整块写回。
    """
    text = section.raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if section.heading:
        body_start = 0
        for i, line in enumerate(lines):
            if _HEADING_RE.match(line) is not None:
                body_start = i + 1
                break
        lines = lines[body_start:]
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip("\n")


def set_raw_section_text(entry: CanonEntry, heading: str, text: str) -> None:
    """整块替换 RAW/FREE 原文区块的正文；标题行不动，正文逐字写回。

    - 重建格式为 标题行 + 空行 + 正文 + 结尾空行；该区块原本使用 CRLF
      行尾时按原行尾风格写回。不加受管标记、不改变分区角色。
    - 与当前正文一致时为无操作（幂等），不触碰原文字节；未编辑的区块
      保存时仍逐字节还原。
    - extras/fields 等结构化数据不受影响：原文区块本就不参与结构化。
    """
    section = section_or_raise(entry, heading)
    if section.role == ROLE_MANAGED:
        raise ValueError("该分区由 DeepSonder 自动同步，表单内只读。")
    if section.role not in (ROLE_RAW, ROLE_FREE):
        raise ValueError(f"分区「{heading}」不是原文区块。")
    body = str(text).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if body == raw_section_body(section):
        return  # 幂等：正文未变化时保持原字节
    newline = "\r\n" if "\r\n" in section.raw else "\n"
    body_text = body.replace("\n", newline) if newline == "\r\n" else body
    if section.heading:
        section.raw = f"## {section.heading}{newline}{newline}{body_text}{newline}{newline}"
        section.marked = False  # 重建后的原文不再携带旧的受管标记
    else:
        # 无标题残片：正文即整段原文（含标记行），整块写回
        section.raw = f"{body_text}{newline}" if body_text else ""


def _clean_extras_lines(lines) -> list[str]:
    """手写行清洗：拆开内嵌换行并 LF 归一；剔除纯空白行。

    纯空白行写回后会因解析跳过空行而丢失，造成表单与文件的来回漂移，
    因此在入口统一剔除。
    """
    cleaned: list[str] = []
    for line in lines:
        for part in str(line).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if part.strip():
                cleaned.append(part)
    return cleaned


def _is_extras_slot(line: str) -> bool:
    """该行是否为手写行槽位：非空、非字段条目、非子分组标题。"""
    stripped = line.rstrip("\r\n")
    return (bool(stripped.strip()) and _SUB_RE.match(stripped) is None
            and _ITEM_RE.match(stripped) is None)


def _rebuild_extras_raw(section: CanonSection, group_name: str | None,
                        new_lines: list[str]) -> str | None:
    """在 raw 原位替换手写行槽位；结构行（条目/子分组/空行/标记）逐字节保留。

    返回 None 表示无法安全手术（raw 无标题行，或目标子分组不在 raw 中），
    调用方应回退为整块规范化渲染（置 dirty）。
    """
    lines = section.raw.splitlines(keepends=True)
    head_end = None
    for i, line in enumerate(lines):
        if _HEADING_RE.match(line.rstrip("\r\n")) is not None:
            head_end = i + 1
            break
    if head_end is None:
        return None
    if group_name is None:
        start = head_end
        stop = len(lines)
        if section.role == ROLE_GROUP:
            # 分组分区顶层手写行只存在于首个 ### 之前（与解析口径一致）
            first_sub = next((i for i in range(start, stop)
                             if _SUB_RE.match(lines[i].rstrip("\r\n")) is not None), None)
            if first_sub is not None:
                stop = first_sub
    else:
        sub = None
        for i in range(head_end, len(lines)):
            match = _SUB_RE.match(lines[i].rstrip("\r\n"))
            if match is not None and match.group(1) == group_name:
                sub = i
                break
        if sub is None:
            return None
        start = sub + 1
        stop = next((i for i in range(start, len(lines))
                     if _SUB_RE.match(lines[i].rstrip("\r\n")) is not None), len(lines))
    if (section.marked and lines and stop >= len(lines) - 1
            and lines[-1].rstrip("\r\n") == SECTION_END):
        stop = len(lines) - 1  # 结尾的受管标记行保持原样
    newline = "\r\n" if "\r\n" in section.raw else "\n"
    out = lines[:start]
    pending = list(new_lines)
    span: list[str] = []
    for line in lines[start:stop]:
        if _is_extras_slot(line):
            if pending:
                ending = line[len(line.rstrip("\r\n")):] or newline
                span.append(pending.pop(0) + ending)
            continue  # 新手写行更少：删除多余槽位
        span.append(line)
    if pending:
        trailing = []
        while span and not span[-1].strip():
            trailing.insert(0, span.pop())
        has_content = any(line.strip() for line in span)
        if not trailing or not has_content:
            span.append(newline)  # 与既有正文之间补一个空行分隔
        for text in pending:
            span.append(text + newline)
        span.extend(trailing)
    out.extend(span)
    out.extend(lines[stop:])
    return "".join(out)


def set_section_extras(entry: CanonEntry, heading: str, lines) -> None:
    """逐字替换字段/分组分区顶部的手写行（extras）。

    分区未处于脏渲染路径时对 raw 做原位手术：字段条目、子分组、空行与
    行尾逐字节保留，仅手写行槽位被替换（extras-only 的编辑不规范化分区
    其余部分，也不引入受管标记）。分区已置脏时只更新数据，由
    _render_dirty 统一重组。内容一致时为无操作（幂等）。
    """
    section = section_or_raise(entry, heading)
    if section.role not in (ROLE_FIELDS, ROLE_GROUP):
        raise ValueError(f"分区「{heading}」不是字段或分组分区，无法替换手写行。")
    new_lines = _clean_extras_lines(lines)
    if new_lines == section.extras:
        return
    section.extras = new_lines
    if not section.dirty:
        rebuilt = _rebuild_extras_raw(section, None, new_lines)
        if rebuilt is not None:
            section.raw = rebuilt
        else:
            section.dirty = True  # 无法手术时回退整块规范化渲染


def set_group_extras(entry: CanonEntry, heading: str, name: str, lines) -> None:
    """逐字替换分组分区中某个条目（### 小节）内的手写行。

    写回语义与 set_section_extras 一致：非脏路径原位手术，脏路径交给
    _render_dirty；内容一致时为无操作（幂等）。
    """
    section = section_or_raise(entry, heading)
    if section.role != ROLE_GROUP:
        raise ValueError(f"分区「{heading}」不是可重复分组分区。")
    group = next((g for g in section.groups if g.name == name), None)
    if group is None:
        raise ValueError(f"分组分区「{heading}」中不存在条目「{name}」。")
    new_lines = _clean_extras_lines(lines)
    if new_lines == group.extras:
        return
    group.extras = new_lines
    if not section.dirty:
        rebuilt = _rebuild_extras_raw(section, name, new_lines)
        if rebuilt is not None:
            section.raw = rebuilt
        else:
            section.dirty = True


# ---------- 用户模块（Phase B）：非系统区块的新建/重命名/删除/移动 ----------

_MODULE_ROLES = (ROLE_FIELDS, ROLE_TEXT, ROLE_GROUP)


def is_system_section(entry: CanonEntry, section: CanonSection) -> bool:
    """系统区块：受管同步区或 schema 声明的固定结构（角色卡基础档案）。

    系统区块的结构（标题、位置、去留）受保护；内容仍可正常编辑。
    """
    if section.role == ROLE_MANAGED:
        return True
    spec = section_spec(entry.kind, section.heading)
    return bool(spec and spec.get("fixed"))


def _clean_module_name(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        raise ValueError("模块名称不能为空。")
    if "\n" in name or "\r" in name:
        raise ValueError("模块名称不能包含换行。")
    return name


def add_module(entry: CanonEntry, name: str, module_role: str,
               position: int | None = None) -> CanonSection:
    """新建用户模块（自定义名称与类型），默认插入到受管区块之前或末尾。

    受管同步区块保持末尾相对位置：未指定 position 时插入到首个受管区块
    之前；显式 position 也会被限制在受管区块之前，越界值收敛到边界。
    """
    name = _clean_module_name(name)
    if module_role not in _MODULE_ROLES:
        raise ValueError("模块类型必须是字段型、文本型或分组型。")
    if name.casefold() in {s.heading.casefold() for s in entry.sections}:
        raise ValueError(f"模块名称「{name}」已存在。")
    first_managed = next((i for i, s in enumerate(entry.sections)
                          if s.role == ROLE_MANAGED), None)
    limit = first_managed if first_managed is not None else len(entry.sections)
    index = limit if position is None else max(0, min(int(position), limit))
    section = CanonSection(heading=name, role=module_role,
                           raw=f"## {name}\n\n", dirty=True)
    entry.sections.insert(index, section)
    return section


def _replace_heading_line(raw: str, old: str, new: str) -> str:
    """把 raw 中的 `## old` 标题行原地替换为 `## new`，行尾与正文不动。"""
    lines = raw.splitlines(keepends=True)
    for i, line in enumerate(lines):
        match = _HEADING_RE.match(line.rstrip("\r\n"))
        if match is not None and match.group(1) == old:
            ending = line[len(line.rstrip("\r\n")):]
            lines[i] = f"## {new}{ending}"
            return "".join(lines)
    return raw


def rename_module(entry: CanonEntry, old: str, new: str) -> None:
    """重命名用户模块：只替换原始字节中的标题行，正文逐字节保留。

    不置 dirty（含 ROLE_RAW 的模块置 dirty 会触发整块规范化甚至无法
    渲染）；raw 即事实源，重命名后保存直接以新标题原样写回。
    """
    section = section_or_raise(entry, old)
    if is_system_section(entry, section):
        raise ValueError("系统区块不可重命名。")
    new = _clean_module_name(new)
    if new == section.heading:
        return
    if new.casefold() in {s.heading.casefold() for s in entry.sections
                          if s is not section}:
        raise ValueError(f"模块名称「{new}」已存在。")
    section.raw = _replace_heading_line(section.raw, section.heading, new)
    section.heading = new


def delete_module(entry: CanonEntry, name: str) -> None:
    """删除用户模块：渲染时该区块整体从 md 文档中移除，其余区块不动。"""
    section = section_or_raise(entry, name)
    if is_system_section(entry, section):
        raise ValueError("系统区块不可删除。")
    entry.sections.remove(section)


def move_module(entry: CanonEntry, name: str, offset: int) -> None:
    """在非系统区块之间上移/下移一位；系统与受管区块位置固定不被穿越。

    相邻位置是系统区块时按同方向继续查找，因此受管同步区块保持原有
    相对位置；已到可移动边界时抛出异常而不是静默失败。
    """
    section = section_or_raise(entry, name)
    if section.role == ROLE_MANAGED:
        raise ValueError("受管区块位置固定，不能移动。")
    if is_system_section(entry, section):
        raise ValueError("系统区块位置固定，不能移动。")
    step = 1 if int(offset) > 0 else -1
    idx = entry.sections.index(section)
    target = idx + step
    while 0 <= target < len(entry.sections) and is_system_section(
            entry, entry.sections[target]):
        target += step
    if not 0 <= target < len(entry.sections):
        raise ValueError("模块已在可移动范围的边界。")
    entry.sections.remove(section)
    entry.sections.insert(target, section)


def load_canon_entry(path: Path | str, kind: str) -> CanonEntry:
    return parse_canon_entry(kind, Path(path).read_text(encoding="utf-8"))


def write_canon_entry(path: Path | str, entry: CanonEntry) -> None:
    atomic_write_text(Path(path), render_canon_entry(entry), encoding="utf-8")


# 受管同步区块中视为“空值”的占位写法（与 character_card_sync 的解析口径一致）
_PLACEHOLDER_VALUES = {"", "无", "未记录"}


def _managed_section_synced(section: CanonSection) -> bool:
    """受管同步区块内是否至少有一行非占位的 `- key：value`（忽略标记注释行）。"""
    for line in section.raw.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("<!--"):
            continue
        item = _ITEM_RE.match(stripped)
        if item is not None and item.group(2).strip() not in _PLACEHOLDER_VALUES:
            return True
    return False


def entry_status(entry: CanonEntry) -> dict:
    """计算一份已解析设定材料的索引徽章状态（缺字段／同步状态／缺分区）。"""
    if entry.kind == "character":
        section = entry.section("基础档案")
        fields = section.fields if section is not None else {}
        missing = sum(1 for key in section_spec("character", "基础档案")["keys"]
                      if not str(fields.get(key) or "").strip())
        managed = next((s for s in entry.sections if s.role == ROLE_MANAGED), None)
        return {"missing_fields": missing,
                "synced": managed is not None and _managed_section_synced(managed)}
    headings = [spec["heading"] for spec in schema_for(entry.kind)["sections"]
                if spec["role"] == ROLE_TEXT]
    missing = 0
    for heading in headings:
        section = next((s for s in entry.sections if s.heading == heading), None)
        if section is not None and not section.text.strip():
            missing += 1
    return {"missing_sections": missing}


def collect_canon_index(project, alias_map: dict[str, str] | None = None) -> list[dict]:
    """汇总项目内全部设定材料为索引条目（角色 → 世界观 → 体系，类内按名称排序）。

    单个文件读取或解析失败时跳过，不阻塞其余材料的索引。
    """
    alias_map = alias_map or {}
    primaries = set(alias_map.values())
    listings = (("character", project.list_characters),
                ("world", project.list_world),
                ("power", project.list_power))
    index: list[dict] = []
    for kind, list_files in listings:
        try:
            files = sorted(list_files(), key=lambda path: path.stem)
        except OSError:
            continue
        for path in files:
            try:
                entry = parse_canon_entry(kind, path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            item = {"kind": kind, "name": path.stem, "path": path, **entry_status(entry)}
            if kind == "character" and alias_map:
                item["identity"] = ("alias" if path.stem in alias_map
                                    else "primary" if path.stem in primaries else None)
            index.append(item)
    return index
