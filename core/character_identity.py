"""Explicit, reversible identities. Raw memories remain owned by their original names."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import re
from uuid import uuid4
from .storage import atomic_write_text

REGISTRY = "_character_identities"
UPDATES = "_character_identity_updates"


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_registry(project) -> dict:
    path = project.memory_dir / "character_identities.json"
    if not path.exists():
        return {"schema_version": 1, "groups": [], "operations": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or not isinstance(value.get("groups"), list) or not isinstance(value.get("operations"), list)):
        raise ValueError("人物身份记录格式无效，请恢复备份后重试。")
    seen = set()
    for group in value["groups"]:
        if (not isinstance(group, dict) or not isinstance(group.get("names"), list)
                or not all(isinstance(n, str) and n.strip() for n in group["names"])
                or group.get("primary") not in group["names"]
                or not isinstance(group.get("id"), str)
                or type(group.get("from_chapter")) is not int
                or not isinstance(group.get("choices"), dict)):
            raise ValueError("人物身份记录不完整，请恢复备份后重试。")
        keys = {n.casefold() for n in group["names"]}
        if keys & seen or len(keys) != len(group["names"]):
            raise ValueError("人物身份名称存在歧义，请恢复备份后重试。")
        seen.update(keys)
    return value


def bind_state(project, state: dict, chapter: int | None = None) -> dict:
    result = deepcopy(state)
    result.pop(REGISTRY, None)
    registry = load_registry(project)
    groups = [g for g in registry["groups"] if chapter is None or g["from_chapter"] <= chapter]
    if registry["operations"] or groups:
        result[REGISTRY] = {"groups": groups, "revision": fingerprint(registry)}
    return result


def canonical_name(state: dict, name: str) -> str:
    for group in state.get(REGISTRY, {}).get("groups", []):
        if name.casefold() in {n.casefold() for n in group["names"]}:
            return group["primary"]
    return name


def identity_names(state: dict, name: str) -> set[str]:
    for group in state.get(REGISTRY, {}).get("groups", []):
        if name.casefold() in {n.casefold() for n in group["names"]}:
            return set(group["names"])
    return {name}


def project_state(state: dict) -> dict:
    """Read view only. Never save this projection over the raw state."""
    result = deepcopy(state)
    characters = result.get("characters")
    if not isinstance(characters, dict) or not state.get(REGISTRY, {}).get("groups"):
        return result
    versions = state.get(UPDATES, {})
    for group in state.get(REGISTRY, {}).get("groups", []):
        keys = {n.casefold() for n in group["names"]}
        members = {n: v for n, v in characters.items() if n.casefold() in keys and isinstance(v, dict)}
        if not members:
            continue
        merged = {}
        for field in {f for details in members.values() for f in details}:
            owners = [n for n, details in members.items() if field in details]
            preferred = group["choices"].get(field, group["primary"])
            baseline = group.get("baseline_updates", {})
            def rank(name):
                version = versions.get(name, {}).get(field, 0)
                fresh = version if version > baseline.get(name, {}).get(field, 0) else 0
                return (fresh, name == preferred, name == group["primary"], name)
            owner = max(owners, key=rank)
            merged[field] = deepcopy(members[owner][field])
        for name in members:
            characters.pop(name)
        characters[group["primary"]] = merged
    for name, details in characters.items():
        if not isinstance(details, dict) or not isinstance(details.get("relations"), dict):
            continue
        relations = {}
        for target, label in details["relations"].items():
            target = canonical_name(state, target)
            if target == name:
                continue
            if target in relations and relations[target] != label:
                relations[target] = f"待核对：{relations[target]} / {label}"
            else:
                relations[target] = label
        details["relations"] = relations
    return result


def conflict_options(state: dict, names: list[str]) -> dict:
    keys = {n.casefold() for n in names}
    members = {n: d for n, d in state.get("characters", {}).items() if n.casefold() in keys and isinstance(d, dict)}
    fields = {f for d in members.values() for f in d}
    return {f: {n: d[f] for n, d in members.items() if f in d}
            for f in sorted(fields)
            if len({fingerprint(d[f]) for d in members.values() if f in d}) > 1}


def save_identity(project, *, primary: str, names: list[str], chapter_id: str,
                  evidence: str, choices: dict, expected_revision: str, expected_state: str) -> str:
    from .project import chapter_number_from_id
    registry = load_registry(project)
    state = project.load_story_state()
    if fingerprint(registry) != expected_revision or fingerprint(state) != expected_state:
        raise ValueError("身份或故事状态已变化，请重新打开身份管理并预览。")
    primary = primary.strip()
    names = list(dict.fromkeys([primary, *(n.strip() for n in names)]))
    if not 2 <= len(names) <= 20 or any(not re.fullmatch(r"[^\r\n\x00]{1,80}", n) for n in names):
        raise ValueError("请填写 2 至 20 个不同的身份名称，每个名称不超过 80 字。")
    if len({n.casefold() for n in names}) != len(names):
        raise ValueError("身份名称不能重复。")
    if chapter_id not in {p.stem for p in project.list_chapters()} or chapter_number_from_id(chapter_id) is None:
        raise ValueError("请选择有效的身份揭露章节。")
    if not evidence.strip() or len(evidence) > 2000:
        raise ValueError("请填写身份关联依据（最多 2000 字）。")
    keys = {n.casefold() for n in names}
    overlaps = [g for g in registry["groups"] if keys & {n.casefold() for n in g["names"]}]
    if len(overlaps) > 1:
        raise ValueError("这些名称属于多个已确认人物，请先撤销有冲突的关联。")
    if overlaps and not set(overlaps[0]["names"]).issubset(names):
        raise ValueError("扩展身份时请保留该人物的全部已有名称；需要拆分时请先撤销关联。")
    for field, options in conflict_options(state, names).items():
        if choices.get(field) not in options:
            raise ValueError(f"请为冲突字段 {field} 选择要采用的身份记录。")
    group = {"id": overlaps[0]["id"] if overlaps else "character_" + uuid4().hex,
             "primary": primary, "names": names, "from_chapter": chapter_number_from_id(chapter_id),
             "chapter_id": chapter_id, "evidence": evidence.strip(), "choices": dict(choices),
             "baseline_updates": deepcopy(state.get(UPDATES, {}))}
    registry["groups"] = [g for g in registry["groups"] if g["id"] != group["id"]] + [group]
    registry["operations"].append({"action": "associate", "before": overlaps, "after": group})
    atomic_write_text(project.memory_dir / "character_identities.json", json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    return group["id"]


def remove_identity(project, group_id: str, expected_revision: str) -> None:
    registry = load_registry(project)
    if fingerprint(registry) != expected_revision:
        raise ValueError("身份记录已变化，请重新打开身份管理。")
    group = next((g for g in registry["groups"] if g["id"] == group_id), None)
    if group is None:
        raise ValueError("身份关联已不存在。")
    registry["groups"].remove(group)
    registry["operations"].append({"action": "remove", "before": group})
    atomic_write_text(project.memory_dir / "character_identities.json", json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def record_update(state: dict, name: str, field: str) -> None:
    versions = state.setdefault(UPDATES, {})
    baselines = [g.get("baseline_updates", {}) for g in state.get(REGISTRY, {}).get("groups", [])]
    sequence = max((v for mapping in [versions, *baselines] for fields in mapping.values() for v in fields.values()), default=0) + 1
    versions.setdefault(name, {})[field] = sequence


def character_details(state: dict, name: str) -> dict:
    """Resolve one character without copying the entire manuscript state per patch."""
    characters = state.get("characters", {})
    if not isinstance(characters, dict):
        return {}
    names = identity_names(state, name)
    if len(names) == 1:
        value = characters.get(name, {})
        return value if isinstance(value, dict) else {}
    keys = {n.casefold() for n in names}
    subset = {REGISTRY: state[REGISTRY], UPDATES: state.get(UPDATES, {}),
              "characters": {n: d for n, d in characters.items() if n.casefold() in keys}}
    return project_state(subset)["characters"].get(canonical_name(state, name), {})
