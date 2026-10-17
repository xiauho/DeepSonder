"""设定材料的字段 Schema 与受管区块序列化器。

设计约束（阶段 1，纯新增）：
- md 文件始终是单一事实源；本模块只提供"解析 ⇄ 渲染"映射，不改变任何现有消费者。
- 未修改的区块渲染时逐字节还原（round-trip 保证），只有被编辑的分区才会被
  重排并以 novalist:section:v1 受管标记包裹（与 auto-state 同款标记风格）。
- 解析容错：无法识别的行、手写内容、未知章节一律原样保留（进入 extras / raw），
  由上层提示用户改用源码模式，绝不丢弃内容。
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
# 模板中的章节标题完全一致；未列出的标题按 ROLE_RAW 原样保留。
FIELD_SCHEMAS: dict[str, dict] = {
    "character": {
        "sections": [
            {"heading": "基础档案", "role": ROLE_FIELDS, "keys": [
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


def _role_for(kind: str, heading: str) -> str:
    for spec in schema_for(kind)["sections"]:
        if spec["heading"] == heading:
            return spec["role"]
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
        role = _role_for(kind, heading)
        section = CanonSection(heading=heading, role=role, raw=raw, marked=marked)
        body = content[content.index(heading_line) + 1:]
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
        raise ValueError(f"分区「{heading}」不是字段分区，请用源码模式编辑。")
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
        raise ValueError(f"分区「{heading}」不是文本分区，请用源码模式编辑。")
    section.text = str(text).strip("\n")
    section.dirty = True


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
