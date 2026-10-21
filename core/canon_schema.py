"""设定材料的字段 Schema 与受管区块序列化器。

设计约束：
- md 文件始终是单一事实源；本模块提供解析、渲染与模块身份映射。
- 未修改的区块渲染时逐字节还原（round-trip 保证），只有被编辑的分区才会被
  重排并以 novalist:section:v1 受管标记包裹（与 auto-state 同款标记风格）。
- 解析容错：无法识别的行、手写内容、未知章节一律原样保留（进入 extras / raw），
  由上层以可编辑的“原文区块”或手写行形式逐字写回，绝不丢弃内容。
- "当前剧情状态（DeepSonder 同步）"为只读同步区块，"作者自由备注"永不结构化，
  二者只做逐字节透传。
"""
from __future__ import annotations

import re
import json
from collections import Counter
from uuid import NAMESPACE_URL, uuid4, uuid5
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from .project_data import CANON_ENTRY_TYPES
from .storage import atomic_write_text

SECTION_START = "<!-- novalist:section:v1:start -->"
SECTION_END = "<!-- novalist:section:v1:end -->"
MODULE_PREFIX = "<!-- deepsonder:module:v1 "
_MODULE_META_RE = re.compile(r"^<!-- deepsonder:module:v1 (.+) -->$")

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


# 体系设定的子类型 Schema。子类型按 system_registry 的 type（ability/space）
# 与常驻核心规则文件名区分；与通用 power Schema 并列为该类型的可选分区集合。
# 解析、状态与表单在“通用 + 全部子类型”中按标题解析，命中即视为该类型已登记
# 的分区，避免子类型标题未登记而被降级成用户模块并虚报“缺分区”。
SUBTYPE_SCHEMAS: dict[str, dict] = {
    "power:ability": {
        "sections": [
            {"heading": "体系定位", "role": ROLE_TEXT},
            {"heading": "等级或层级", "role": ROLE_TEXT},
            {"heading": "核心原则", "role": ROLE_TEXT},
            {"heading": "获取方式", "role": ROLE_TEXT},
            {"heading": "使用限制", "role": ROLE_TEXT},
            {"heading": "代价与副作用", "role": ROLE_TEXT},
            {"heading": "克制关系", "role": ROLE_TEXT},
            {"heading": "晋升与突破", "role": ROLE_TEXT},
            {"heading": "已知例外", "role": ROLE_TEXT},
            {"heading": "对剧情的约束", "role": ROLE_TEXT},
        ],
    },
    "power:space": {
        "sections": [
            {"heading": "体系定位", "role": ROLE_TEXT},
            {"heading": "空间层级", "role": ROLE_TEXT},
            {"heading": "空间之间的关系", "role": ROLE_TEXT},
            {"heading": "进入与离开条件", "role": ROLE_TEXT},
            {"heading": "时间流速差异", "role": ROLE_TEXT},
            {"heading": "空间边界与限制", "role": ROLE_TEXT},
            {"heading": "空间资源", "role": ROLE_TEXT},
            {"heading": "与主世界的关系", "role": ROLE_TEXT},
            {"heading": "对剧情的约束", "role": ROLE_TEXT},
            {"heading": "已知例外", "role": ROLE_TEXT},
        ],
    },
    "power:core_rules": {
        "sections": [
            {"heading": "全书不可违背的规则", "role": ROLE_TEXT},
            {"heading": "基础约束条件", "role": ROLE_TEXT},
            {"heading": "生效与获取条件", "role": ROLE_TEXT},
            {"heading": "代价与副作用", "role": ROLE_TEXT},
            {"heading": "全局例外", "role": ROLE_TEXT},
            {"heading": "对剧情的约束", "role": ROLE_TEXT},
        ],
    },
}


def schema_for(kind: str) -> dict:
    normalized = str(kind or "").strip().lower()
    if normalized not in FIELD_SCHEMAS or normalized not in CANON_ENTRY_TYPES:
        raise ValueError(f"未知的设定材料类型：{kind}")
    return FIELD_SCHEMAS[normalized]


def kind_schemas(kind: str) -> list[dict]:
    """该类型可用的全部分区 Schema：通用 Schema 在前，子类型 Schema 随后。"""
    normalized = str(kind or "").strip().lower()
    schemas = [schema_for(normalized)]
    schemas.extend(schema for key, schema in SUBTYPE_SCHEMAS.items()
                   if key.split(":", 1)[0] == normalized)
    return schemas


def section_spec(kind: str, heading: str | CanonSection) -> dict | None:
    """返回某分区声明的角色与字段键（供表单渲染缺失字段）。

    先在通用 Schema 中查找，再回退到该类型的子类型 Schema，因此子类型材料
    的标题无需显式传入子类型即可被识别为已登记分区。
    """
    if isinstance(heading, CanonSection):
        if heading.has_metadata and not heading.schema_key:
            return None
        heading = heading.schema_key or heading.heading
    for schema in kind_schemas(kind):
        for spec in schema["sections"]:
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
      无法按单一角色无损渲染，一律保持 ROLE_RAW 整块编辑。
    未编辑的模块渲染时逐字节还原，类型猜测不影响 round-trip。
    """
    lines = [line.strip() for line in str(raw_body).splitlines()]
    body = [line for line in lines if line]
    if not body:
        return ROLE_TEXT
    if any(line.startswith(("|", "```", "~~~")) or "<!--" in line for line in body):
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
    module_id: str = dataclass_field(default_factory=lambda: uuid4().hex)
    schema_key: str = ""
    declared_role: str = ""
    has_metadata: bool = False
    suffix: str = ""          # Content outside a closed managed span, anchored in the document gap.
    newline: str = "\n"
    warning: str = ""
    opaque: bool = False


@dataclass
class CanonEntry:
    kind: str
    title: str
    preamble: str           # 首个章节之前的全部原文（含 # 标题行与卡片标记注释）
    sections: list[CanonSection] = dataclass_field(default_factory=list)
    newline: str = "\n"

    def section(self, heading: str) -> CanonSection | None:
        return next((s for s in self.sections if s.heading == heading), None)


def _newline(text: str) -> str:
    match = re.search(r"\r\n|\r|\n", text)
    return match.group(0) if match else "\n"


def _normal(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _unclosed_comment(value: str, position: int = 0) -> bool:
    while True:
        opening = value.find("<!--", position)
        if opening < 0:
            return False
        closing = value.find("-->", opening + 4)
        if closing < 0:
            return True
        position = closing + 3


def _active_lines(lines: list[str], *, front_matter: bool = True) -> list[bool]:
    """Identify block syntax outside fences, front matter, and opaque HTML."""
    active = []
    fence = None
    comment = False
    html_tag = None
    frontmatter = front_matter and bool(lines and lines[0].lstrip("\ufeff").rstrip("\r\n") == "---")
    for index, line in enumerate(lines):
        value = line.rstrip("\r\n")
        if frontmatter:
            active.append(False)
            if index and value in {"---", "..."}:
                frontmatter = False
            continue
        if fence is not None:
            active.append(False)
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(fence[1]) + r",}\s*", value):
                fence = None
            continue
        if comment:
            active.append(False)
            closing = value.find("-->")
            if closing >= 0:
                comment = _unclosed_comment(value, closing + 3)
            continue
        if html_tag:
            active.append(False)
            if re.search(r"</" + html_tag + r"\s*>", value, re.I):
                html_tag = None
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", value)
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            fence = (opening.group(1)[0], len(opening.group(1)))
            active.append(False)
            continue
        active.append(True)
        comment = _unclosed_comment(value)
        tag = re.match(r"^ {0,3}<(pre|script|style|textarea)(?:\s|>)", value, re.I)
        if tag and not re.search(r"</" + tag.group(1) + r"\s*>", value, re.I):
            html_tag = tag.group(1).lower()
    return active


def _metadata(line: str) -> dict | None:
    match = _MODULE_META_RE.fullmatch(line.rstrip("\r\n"))
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except (ValueError, TypeError):
        return None
    roles = {ROLE_FIELDS, ROLE_GROUP, ROLE_TEXT, ROLE_RAW, ROLE_FREE}
    if (not isinstance(data, dict) or set(data) - {"id", "role", "schema"}
            or not isinstance(data.get("id"), str)
            or re.fullmatch(r"[0-9a-f]{32}", data["id"]) is None
            or not isinstance(data.get("role"), str) or data["role"] not in roles
            or not isinstance(data.get("schema", ""), str)):
        return None
    return data


def _metadata_line(section: CanonSection) -> str:
    payload = {"id": section.module_id, "role": section.declared_role or section.role,
               "schema": section.schema_key}
    return MODULE_PREFIX + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + " -->"


def _layout(section: CanonSection):
    lines = section.raw.splitlines(keepends=True)
    active = _active_lines(lines)
    heading = next((i for i, line in enumerate(lines)
                    if active[i] and _HEADING_RE.match(line.rstrip("\r\n"))), None)
    start = heading + 1 if heading is not None else 0
    end = len(lines)
    if section.marked:
        end = next((i for i in range(start, len(lines))
                    if active[i] and lines[i].rstrip("\r\n") == SECTION_END), len(lines))
    return lines, heading, start, end


def _ensure_identity(section: CanonSection) -> None:
    if not section.heading or section.opaque or section.role == ROLE_MANAGED:
        return
    lines, heading, _start, _end = _layout(section)
    if heading is None:
        return
    candidates = [i for i in range(heading) if lines[i].startswith("<!-- deepsonder:module:")]
    if candidates:
        lines[candidates[0]] = _metadata_line(section) + section.newline
    else:
        lines.insert(heading, _metadata_line(section) + section.newline)
    section.raw = "".join(lines)
    section.has_metadata = True


def _structured_safe(role: str, body: list[str]) -> bool:
    group = None
    names = set()
    keys = set()
    for line in body:
        value = line.rstrip("\r\n")
        if not value.strip():
            continue
        if (value.lstrip().startswith(("```", "~~~", "|", "<!--"))
                or "<!--" in value or value.startswith(("  ", "\t"))):
            return False
        sub = _SUB_RE.match(value)
        if sub:
            if role != ROLE_GROUP or sub.group(1).casefold() in names:
                return False
            group = sub.group(1)
            names.add(group.casefold())
            keys = set()
            continue
        item = _ITEM_RE.match(value)
        if item:
            key = item.group(1).strip()
            if (role == ROLE_GROUP and group is None) or key in keys:
                return False
            keys.add(key)
    return True


def parse_canon_entry(kind: str, text: str) -> CanonEntry:
    """Parse source spans; opening and no-op rendering never rewrite the document."""
    schema_for(kind)
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    source = text[len(bom):]
    lines = source.splitlines(keepends=True)
    active = _active_lines(lines)
    starts = []
    i = 0
    while i < len(lines):
        value = lines[i].rstrip("\r\n")
        if not active[i]:
            i += 1
            continue
        if value == SECTION_START:
            boundary = next((k for k in range(i + 1, len(lines)) if active[k]
                             and lines[k].rstrip("\r\n") in {SECTION_START, SECTION_END}), None)
            if boundary is not None and lines[boundary].rstrip("\r\n") == SECTION_END:
                starts.append((i, True, boundary))
                i = boundary + 1
                continue
        if value.startswith("<!-- deepsonder:module:"):
            next_line = i + 1
            while next_line < len(lines) and not lines[next_line].strip():
                next_line += 1
            if next_line < len(lines) and active[next_line] and _HEADING_RE.match(lines[next_line].rstrip("\r\n")):
                starts.append((i, False, None))
                i = next_line + 1
                continue
        if _HEADING_RE.match(value):
            starts.append((i, False, None))
        i += 1

    preamble = bom + "".join(lines[:starts[0][0]]) if starts else text
    title = ""
    pre_lines = preamble.splitlines(keepends=True)
    for line, enabled in zip(pre_lines, _active_lines(pre_lines)):
        match = re.match(r"^\ufeff?#\s+(.+?)\s*$", line.rstrip("\r\n")) if enabled else None
        if match:
            title = match.group(1)
            break
    entry = CanonEntry(kind, title, preamble, newline=_newline(text))
    occurrences = Counter()
    for index, (start, marked, closing) in enumerate(starts):
        stop = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        raw = "".join(lines[start:stop])
        content = lines[start + 1:closing] if marked else lines[start:stop]
        content_active = _active_lines(content)
        head = next((j for j, line in enumerate(content) if content_active[j]
                     and _HEADING_RE.match(line.rstrip("\r\n"))), None)
        heading = _HEADING_RE.match(content[head].rstrip("\r\n")).group(1) if head is not None else ""
        meta_lines = [line for line in content[:head] if line.startswith("<!-- deepsonder:module:")] if head is not None else []
        data = _metadata(meta_lines[0]) if len(meta_lines) == 1 else None
        invalid_meta = bool(meta_lines) and data is None
        spec = section_spec(kind, heading)
        schema_key = spec["heading"] if spec else ""
        if data is not None:
            schema_key = data.get("schema", "")
            known = section_spec(kind, schema_key) if schema_key else None
            if schema_key and known is None:
                invalid_meta = True
            # Reserved system headings retain their protections, regardless of metadata.
            if spec and (spec.get("fixed") or spec["role"] == ROLE_MANAGED):
                schema_key = spec["heading"]
            else:
                spec = known
        body = content[head + 1:] if head is not None else content
        role = data["role"] if data and not invalid_meta else spec["role"] if spec else detect_module_role(heading, "".join(body))
        if spec and spec["role"] == ROLE_MANAGED:
            role = ROLE_MANAGED
        declared = role
        opaque_prefix = any(line.strip() and _metadata(line) is None for line in content[:head]) if head is not None else True
        warning = ""
        if invalid_meta or opaque_prefix:
            role = ROLE_RAW
            warning = "模块结构或元信息存在歧义，已保留原文。"
        elif role in {ROLE_FIELDS, ROLE_GROUP} and not _structured_safe(role, body):
            role = ROLE_RAW
            warning = "存在重复字段、重复条目或混合结构，已保留原文。"
        protected_state = spec is not None and spec["role"] == ROLE_MANAGED
        protected_state = protected_state or any(
            enabled and line.rstrip("\r\n") in {
                "<!-- novalist:auto-state:v1:start -->", "<!-- novalist:auto-state:v1:end -->"}
            for line, enabled in zip(body, _active_lines(body)))
        if protected_state:
            role = ROLE_MANAGED
            declared = ROLE_MANAGED
        key = raw
        occurrences[key] += 1
        identity = data["id"] if data and not invalid_meta else uuid5(NAMESPACE_URL, "DeepSonder-PySide6:" + raw + ":" + str(occurrences[key])).hex
        section = CanonSection(heading, role, raw, marked=marked, module_id=identity,
                               schema_key=schema_key, declared_role=declared,
                               has_metadata=bool(data) and not invalid_meta,
                               suffix="".join(lines[closing + 1:stop]) if marked else "",
                               newline=_newline(raw), warning=warning,
                               opaque=head is None or invalid_meta or opaque_prefix)
        if role in {ROLE_FIELDS, ROLE_GROUP}:
            _parse_structured(section, body)
        elif role == ROLE_TEXT:
            section.text = _normal("".join(body)).strip("\n")
        entry.sections.append(section)
    counts = Counter(s.module_id for s in entry.sections)
    for index, section in enumerate(entry.sections):
        if counts[section.module_id] > 1:
            section.module_id = uuid5(NAMESPACE_URL, section.module_id + ":" + str(index)).hex
            if section.role != ROLE_MANAGED:
                section.role = ROLE_RAW
            section.warning = "模块身份重复，已隔离为原文区块。"
    return entry


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
    parts = [entry.preamble]
    for section in entry.sections:
        value = section.raw if not section.dirty else _render_dirty(section)
        if parts and parts[-1] and not parts[-1].endswith(("\n", "\r")):
            parts.append(entry.newline * 2)
        parts.append(value)
    return "".join(parts)


def _render_dirty(section: CanonSection) -> str:
    if section.role == ROLE_FIELDS:
        lines = [f"## {section.heading}", ""]
        lines += [f"- {key}：{value}" for key, value in section.fields.items()]
        lines += section.extras
    elif section.role == ROLE_GROUP:
        lines = [f"## {section.heading}", "", *section.extras]
        for group in section.groups:
            lines += [f"### {group.name}", ""]
            lines += [f"- {key}：{value}" for key, value in group.fields.items()]
            lines += group.extras
    elif section.role == ROLE_TEXT:
        lines = [f"## {section.heading}"]
        if section.text:
            lines += ["", section.text.replace("\n", section.newline)]
    else:
        raise ValueError(f"分区「{section.heading}」为只读或原文内容，不支持字段化写回。")
    _validate_body("\n".join(lines[1:]))
    return _wrap_body(section, lines)


def _wrap_body(section: CanonSection, lines: list[str]) -> str:
    raw_lines, _head, _start, end = _layout(section)
    if section.marked and end < len(raw_lines):
        tail = raw_lines[end][len(raw_lines[end].rstrip("\r\n")):]
    else:
        core = section.raw[:-len(section.suffix)] if section.suffix else section.raw
        match = re.search(r"(?:\r\n|\r|\n)+$", core)
        tail = match.group(0) if match else ""
    metadata = [_metadata_line(section)] if section.has_metadata else []
    return section.newline.join([SECTION_START, *metadata, *lines, SECTION_END]) + tail + section.suffix


def section_or_raise(entry: CanonEntry, reference: str | CanonSection) -> CanonSection:
    identity = reference.module_id if isinstance(reference, CanonSection) else reference
    section = next((s for s in entry.sections if s.module_id == identity), None)
    if section is not None:
        return section
    matches = [s for s in entry.sections if s.heading == reference]
    if not matches:
        raise ValueError(f"材料中不存在分区「{reference}」。")
    if len(matches) != 1:
        raise ValueError("分区标题不唯一，请按模块身份选择同名分区。")
    return matches[0]


def set_section_field(entry: CanonEntry, heading: str, key: str, value: str) -> None:
    section = section_or_raise(entry, heading)
    if section.role == ROLE_MANAGED:
        raise ValueError("该分区由 DeepSonder 自动同步，表单内只读。")
    if section.role != ROLE_FIELDS:
        raise ValueError(f"分区「{heading}」不是字段分区，无法以字段方式编辑。")
    section.fields[key] = str(value)
    _ensure_identity(section)
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
    _ensure_identity(section)
    section.dirty = True


def set_section_text(entry: CanonEntry, heading: str, text: str) -> None:
    section = section_or_raise(entry, heading)
    if section.role == ROLE_MANAGED:
        raise ValueError("该分区由 DeepSonder 自动同步，表单内只读。")
    if section.role != ROLE_TEXT:
        raise ValueError(f"分区「{heading}」不是文本分区，无法以文本方式编辑。")
    body = _normal(str(text)).strip("\n")
    _validate_body(body)
    section.text = body
    _ensure_identity(section)
    section.dirty = True


# ---------- 原文区块与手写行（Phase C）：md 完全后台化的逐字写回 ----------


def _validate_body(text: str) -> None:
    probe = text + ("" if text.endswith(("\n", "\r")) else "\n") + "DeepSonder-end-probe\n"
    if not _active_lines(probe.splitlines(keepends=True), front_matter=False)[-1]:
        raise ValueError("正文存在未闭合的代码区块、注释或HTML区块，请补全后再保存。")
    lines = text.splitlines(keepends=True)
    if any(enabled and (line.rstrip("\r\n") in {SECTION_START, SECTION_END}
                        or line.startswith("<!-- deepsonder:module:"))
           for line, enabled in zip(lines, _active_lines(lines, front_matter=False))):
        raise ValueError("正文含有分区边界标记，请移除该标记或将示例放入代码区块。")


def raw_section_body(section: CanonSection) -> str:
    if section.opaque:
        core = section.raw[:-len(section.suffix)] if section.suffix else section.raw
        return _normal(core).strip("\n")
    lines, _heading, start, end = _layout(section)
    return _normal("".join(lines[start:end])).strip("\n")


def set_raw_section_text(entry: CanonEntry, heading: str, text: str) -> None:
    section = section_or_raise(entry, heading)
    if section.role == ROLE_MANAGED:
        raise ValueError("该分区由 DeepSonder 自动同步，表单内只读。")
    if section.role not in {ROLE_RAW, ROLE_FREE}:
        raise ValueError(f"分区「{section.heading}」不是原文区块。")
    body = _normal(str(text)).strip("\n")
    if body == raw_section_body(section):
        return
    if section.opaque:
        core = section.raw[:-len(section.suffix)] if section.suffix else section.raw
        ending = re.search(r"(?:\r\n|\r|\n)*$", core).group(0)
        section.raw = body.replace("\n", section.newline) + ending + section.suffix
        return
    _validate_body(body)
    _ensure_identity(section)
    lines = [f"## {section.heading}", "", body.replace("\n", section.newline)]
    section.raw = _wrap_body(section, lines)
    section.marked = True
    section.dirty = False


def visible_canon_text(text: str) -> str:
    """Remove only valid application metadata from prompt text, never code samples."""
    lines = text.splitlines(keepends=True)
    active = _active_lines(lines)
    return "".join(line for line, enabled in zip(lines, active)
                   if not (enabled and _metadata(line) is not None))


def preamble_blocks(entry: CanonEntry) -> list[tuple[int, int, str]]:
    source = entry.preamble
    spans = []
    if source.startswith("\ufeff"):
        spans.append((0, 1))
    lines = source.splitlines(keepends=True)
    offset = 0
    title_seen = False
    for line, enabled in zip(lines, _active_lines(lines)):
        if not title_seen and enabled and re.match(r"^\ufeff?#\s+", line):
            spans.append((offset, offset + len(line)))
            title_seen = True
        offset += len(line)
    if lines and lines[0].lstrip("\ufeff").rstrip("\r\n") == "---":
        closing = next((i for i in range(1, len(lines))
                        if lines[i].rstrip("\r\n") in {"---", "..."}), None)
        if closing is not None:
            spans.append((0, sum(len(line) for line in lines[:closing + 1])))
    offset = 0
    for line, enabled in zip(lines, _active_lines(lines)):
        if enabled and re.match(r"^ {0,3}<!--\s*(?:novalist:|deepsonder:)", line):
            match = re.match(r".*?-->", source[offset:], re.S)
            if match is not None:
                spans.append((offset, offset + match.end()))
        offset += len(line)
    merged = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    blocks = []
    start = 0
    for left, right in merged:
        blocks.append((start, left, _normal(source[start:left]).strip("\n")))
        start = right
    blocks.append((start, len(source), _normal(source[start:]).strip("\n")))
    return blocks


def _validate_loose_text(text: str) -> None:
    _validate_body(text)
    lines = text.splitlines(keepends=True)
    if any(enabled and re.match(r"^#{1,2}\s+", line)
           for line, enabled in zip(lines, _active_lines(lines, front_matter=False))):
        raise ValueError("导语或区块间备注中请勿新增资料标题；请使用新建模块入口。")
    if any(enabled and re.search(r"<!--\s*(?:novalist:|deepsonder:)", line)
           for line, enabled in zip(lines, _active_lines(lines, front_matter=False))):
        raise ValueError("资料标记由应用维护，请勿在导语或备注中写入该标记。")


def set_preamble_text(entry: CanonEntry, index: int, text: str) -> None:
    blocks = preamble_blocks(entry)
    if not 0 <= index < len(blocks):
        raise ValueError("导语位置已变化，请重新载入资料后编辑。")
    start, end, original = blocks[index]
    body = _normal(str(text)).strip("\n")
    if body == original:
        return
    _validate_loose_text(body)
    raw = entry.preamble[start:end]
    leading = re.match(r"^(?:\r\n|\r|\n)*", raw).group(0)
    trailing = re.search(r"(?:\r\n|\r|\n)*$", raw).group(0)
    if not raw:
        leading = ("" if not entry.preamble or entry.preamble.endswith(entry.newline * 2)
                   else entry.newline if entry.preamble.endswith(entry.newline)
                   else entry.newline * 2)
        trailing = entry.newline * 2
    elif not body and not leading and not trailing:
        trailing = entry.newline * 2
    replacement = leading + body.replace("\n", entry.newline) + trailing
    entry.preamble = entry.preamble[:start] + replacement + entry.preamble[end:]


def _replace_suffix(section: CanonSection, value: str) -> None:
    if value.strip() and not section.marked and not section.opaque:
        _validate_body(raw_section_body(section))
        _ensure_identity(section)
        core = section.raw[:-len(section.suffix)] if section.suffix else section.raw
        separator = "" if core.endswith(("\r", "\n")) else section.newline
        section.raw = (SECTION_START + section.newline + core + separator
                       + SECTION_END + section.newline + value)
        section.marked = True
    else:
        core = section.raw[:-len(section.suffix)] if section.suffix else section.raw
        section.raw = core + value
    section.suffix = value


def set_section_suffix(entry: CanonEntry, reference: str, text: str) -> None:
    section = section_or_raise(entry, reference)
    _validate_loose_text(text)
    leading = re.match(r"^(?:\r\n|\r|\n)*", section.suffix).group(0)
    trailing = re.search(r"(?:\r\n|\r|\n)*$", section.suffix).group(0)
    body = _normal(text).strip("\n")
    if body == _normal(section.suffix).strip("\n"):
        return
    _replace_suffix(section, leading + body.replace("\n", section.newline) + trailing)


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
    if section.marked:
        # The end marker may be followed by blank lines or an untouched suffix.
        # Neither belongs to editable handwritten slots.
        marker_end = next((i for i in range(start, stop)
                           if lines[i].rstrip("\r\n") == SECTION_END), None)
        if marker_end is not None:
            stop = marker_end
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
    _validate_loose_text("\n".join(new_lines))
    section.extras = new_lines
    if not section.dirty:
        rebuilt = _rebuild_extras_raw(section, None, new_lines)
        if rebuilt is not None:
            section.raw = rebuilt
        else:
            section.dirty = True  # 无法手术时回退整块规范化渲染
    _ensure_identity(section)


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
    _validate_loose_text("\n".join(new_lines))
    group.extras = new_lines
    if not section.dirty:
        rebuilt = _rebuild_extras_raw(section, name, new_lines)
        if rebuilt is not None:
            section.raw = rebuilt
        else:
            section.dirty = True
    _ensure_identity(section)


# ---------- 用户模块（Phase B）：非系统区块的新建/重命名/删除/移动 ----------

_MODULE_ROLES = (ROLE_FIELDS, ROLE_TEXT, ROLE_GROUP)


def is_system_section(entry: CanonEntry, section: CanonSection) -> bool:
    """系统区块：受管同步区或 schema 声明的固定结构（角色卡基础档案）。

    系统区块的结构（标题、位置、去留）受保护；内容仍可正常编辑。
    """
    if section.role == ROLE_MANAGED:
        return True
    spec = section_spec(entry.kind, section)
    return bool(spec and spec.get("fixed"))


def _clean_module_name(name: str) -> str:
    name = str(name or "").strip()
    if not name:
        raise ValueError("模块名称不能为空。")
    if "\n" in name or "\r" in name:
        raise ValueError("模块名称不能包含换行。")
    _validate_body("## " + name)
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
                           raw=f"## {name}{entry.newline}{entry.newline}", dirty=True,
                           declared_role=module_role, has_metadata=True, newline=entry.newline)
    entry.sections.insert(index, section)
    return section


def _replace_heading_line(raw: str, old: str, new: str) -> str:
    """把 raw 中的 `## old` 标题行原地替换为 `## new`，行尾与正文不动。"""
    lines = raw.splitlines(keepends=True)
    for i, (line, enabled) in enumerate(zip(lines, _active_lines(lines))):
        match = _HEADING_RE.match(line.rstrip("\r\n")) if enabled else None
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
    if not section.heading:
        raise ValueError("该原文区块缺少可识别标题，请先修复原文再重命名。")
    new = _clean_module_name(new)
    if new == section.heading:
        return
    if new.casefold() in {s.heading.casefold() for s in entry.sections
                          if s is not section}:
        raise ValueError(f"模块名称「{new}」已存在。")
    section.raw = _replace_heading_line(section.raw, section.heading, new)
    section.heading = new
    _ensure_identity(section)


def delete_module(entry: CanonEntry, name: str) -> None:
    """删除用户模块：渲染时该区块整体从 md 文档中移除，其余区块不动。"""
    section = section_or_raise(entry, name)
    if is_system_section(entry, section):
        raise ValueError("系统区块不可删除。")
    index = next(i for i, item in enumerate(entry.sections) if item is section)
    if section.suffix:
        if index:
            previous = entry.sections[index - 1]
            _replace_suffix(previous, previous.suffix + section.suffix)
        else:
            entry.preamble += section.suffix
    del entry.sections[index]


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
    idx = next(i for i, item in enumerate(entry.sections) if item is section)
    target = idx + step
    while 0 <= target < len(entry.sections) and is_system_section(
            entry, entry.sections[target]):
        target += step
    if not 0 <= target < len(entry.sections):
        raise ValueError("模块已在可移动范围的边界。")
    suffixes = [item.suffix for item in entry.sections]
    del entry.sections[idx]
    entry.sections.insert(target, section)
    for item, suffix in zip(entry.sections, suffixes):
        _replace_suffix(item, suffix)
    _ensure_identity(section)


def load_canon_entry(path: Path | str, kind: str) -> CanonEntry:
    return parse_canon_entry(kind, Path(path).read_bytes().decode("utf-8"))


def write_canon_entry(path: Path | str, entry: CanonEntry) -> None:
    atomic_write_text(Path(path), render_canon_entry(entry), encoding="utf-8")


# 受管同步区块中视为“空值”的占位写法（与 character_card_sync 的解析口径一致）
_PLACEHOLDER_VALUES = {"", "无", "未记录"}


def _managed_section_synced(section: CanonSection) -> bool:
    """受管同步区块内是否至少有一行非占位的 `- key：value`（忽略标记注释行）。"""
    for line in raw_section_body(section).splitlines():
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
    headings = [spec["heading"] for spec in _status_schema(entry)["sections"]
                if spec["role"] == ROLE_TEXT]
    missing = 0
    for heading in headings:
        section = next((s for s in entry.sections if s.schema_key == heading
                        or (not s.has_metadata and s.heading == heading)), None)
        if section is None or not section.text.strip():
            missing += 1
    return {"missing_sections": missing}


def _status_schema(entry: CanonEntry) -> dict:
    """选择最贴合该材料的分区 Schema。

    通用 Schema 与子类型 Schema（能力／空间／常驻核心规则）会大量重名，
    因此以“已登记分区命中数”优先、并列时取分区更少者，避免把子类型材料
    误判为通用体系后虚报缺分区。
    """
    best = kind_schemas(entry.kind)[0]
    best_score = None
    for schema in kind_schemas(entry.kind):
        headings = [spec["heading"] for spec in schema["sections"]
                    if spec["role"] == ROLE_TEXT]
        matched = sum(1 for heading in headings
                      if any(s.schema_key == heading
                             or (not s.has_metadata and s.heading == heading)
                             for s in entry.sections))
        score = (matched, -len(headings))
        if best_score is None or score > best_score:
            best_score, best = score, schema
    return best


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
