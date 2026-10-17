"""Validated field and group operations on a canon draft."""
from __future__ import annotations

from .canon_schema import (
    CanonEntry, CanonGroup, CanonSection, ROLE_FIELDS, ROLE_GROUP,
    _clean_module_name, _ensure_identity, section_or_raise, section_spec,
)


def _target(entry: CanonEntry, reference: str, group: str | None):
    section = section_or_raise(entry, reference)
    if group is None:
        if section.role != ROLE_FIELDS:
            raise ValueError("该模块不支持字段管理。")
        return section, section.fields
    if section.role != ROLE_GROUP:
        raise ValueError("该模块不支持条目管理。")
    item = next((item for item in section.groups if item.name == group), None)
    if item is None:
        raise ValueError("条目已变化，请重新选择。")
    return section, item.fields


def _declared(entry: CanonEntry, section: CanonSection) -> list[str]:
    return list((section_spec(entry.kind, section) or {}).get("keys", []))


def _label(value: str) -> str:
    name = _clean_module_name(value)
    if len(name.splitlines()) != 1 or "<!--" in name:
        raise ValueError("名称不能含有换行或文档注释标记。")
    return name


def _field_name(value: str) -> str:
    name = _label(value)
    if any(c in name for c in (":", "：")) or name.startswith(("#", "-")):
        raise ValueError("字段名称不能以标题或列表标记开头，也不能含有冒号。")
    return name


def _touch(section: CanonSection) -> None:
    _ensure_identity(section)
    section.dirty = True


def add_field(entry: CanonEntry, reference: str, name: str, group: str | None = None) -> None:
    section, fields = _target(entry, reference, group)
    name = _field_name(name)
    if name.casefold() in {k.casefold() for k in [*fields, *_declared(entry, section)]}:
        raise ValueError(f"字段「{name}」已存在或为模板预设字段。")
    fields[name] = ""
    _touch(section)


def _editable_field(entry, section, fields, key):
    if key in _declared(entry, section):
        raise ValueError("模板预设字段的名称和位置受保护；可编辑其内容。")
    if key not in fields:
        raise ValueError("字段已变化，请重新选择。")


def rename_field(entry: CanonEntry, reference: str, key: str, new: str,
                 group: str | None = None) -> None:
    section, fields = _target(entry, reference, group)
    _editable_field(entry, section, fields, key)
    new = _field_name(new)
    if new == key:
        return
    if new.casefold() in {k.casefold() for k in [*fields, *_declared(entry, section)] if k != key}:
        raise ValueError(f"字段「{new}」已存在。")
    changed = {(new if k == key else k): value for k, value in fields.items()}
    fields.clear()
    fields.update(changed)
    _touch(section)


def delete_field(entry: CanonEntry, reference: str, key: str, group: str | None = None) -> None:
    section, fields = _target(entry, reference, group)
    _editable_field(entry, section, fields, key)
    del fields[key]
    _touch(section)


def move_field(entry: CanonEntry, reference: str, key: str, offset: int,
               group: str | None = None) -> None:
    section, fields = _target(entry, reference, group)
    _editable_field(entry, section, fields, key)
    custom = [k for k in fields if k not in _declared(entry, section)]
    index = custom.index(key)
    target = index + (1 if offset > 0 else -1)
    if not 0 <= target < len(custom):
        raise ValueError("字段已在可移动范围的边界。")
    custom[index], custom[target] = custom[target], custom[index]
    order = iter(custom)
    changed = {(next(order) if k in custom else k): None for k in fields}
    changed = {k: fields[k] for k in changed}
    fields.clear()
    fields.update(changed)
    _touch(section)


def _group_section(entry: CanonEntry, reference: str) -> CanonSection:
    section = section_or_raise(entry, reference)
    if section.role != ROLE_GROUP:
        raise ValueError("该模块不支持条目管理。")
    return section


def _group(section: CanonSection, name: str) -> CanonGroup:
    item = next((item for item in section.groups if item.name == name), None)
    if item is None:
        raise ValueError("条目已变化，请重新选择。")
    return item


def _group_name(section: CanonSection, name: str, old: str | None = None) -> str:
    name = _label(name)
    if name.casefold() in {g.name.casefold() for g in section.groups if g.name != old}:
        raise ValueError(f"条目「{name}」已存在。")
    return name


def add_group(entry: CanonEntry, reference: str, name: str) -> None:
    section = _group_section(entry, reference)
    name = _group_name(section, name)
    section.groups.append(CanonGroup(name, {key: "" for key in _declared(entry, section)}))
    _touch(section)


def rename_group(entry: CanonEntry, reference: str, name: str, new: str) -> None:
    section = _group_section(entry, reference)
    item = _group(section, name)
    new = _group_name(section, new, old=name)
    if new == name:
        return
    item.name = new
    _touch(section)


def delete_group(entry: CanonEntry, reference: str, name: str) -> None:
    section = _group_section(entry, reference)
    section.groups.remove(_group(section, name))
    _touch(section)


def move_group(entry: CanonEntry, reference: str, name: str, offset: int) -> None:
    section = _group_section(entry, reference)
    index = section.groups.index(_group(section, name))
    target = index + (1 if offset > 0 else -1)
    if not 0 <= target < len(section.groups):
        raise ValueError("条目已在可移动范围的边界。")
    section.groups[index], section.groups[target] = section.groups[target], section.groups[index]
    _touch(section)
