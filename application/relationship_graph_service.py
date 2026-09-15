"""Read-only relationship projection over authoritative Novalist project data."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any

from core.accepted_memory import AcceptedMemoryView
from core.project import NovelProject


@dataclass(frozen=True)
class GraphEvidence:
    chapter_id: str
    anchor: str
    description: str
    certainty: str


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    name: str
    aliases: tuple[str, ...]
    state: str
    location: str
    resolved: bool
    path: str | None
    relative_path: str | None
    node_kind: str = "character"
    order: int | None = None


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    source: str
    target: str
    label: str
    source_kind: str
    evidence: tuple[GraphEvidence, ...]
    edge_kind: str = "relationship"


@dataclass(frozen=True)
class GraphWarning:
    code: str
    message: str
    subject: str
    target: str


@dataclass(frozen=True)
class RelationshipGraphSnapshot:
    project_name: str
    revision: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    warnings: tuple[GraphWarning, ...]
    relation_types: tuple[str, ...]


@dataclass(frozen=True)
class _Card:
    name: str
    aliases: tuple[str, ...]
    path: Path
    relations: tuple[tuple[str, str], ...]


class RelationshipGraphService:
    """Build a deterministic graph without modifying project or cache files.

    Current ``story_state`` relationships are authoritative. Character-card
    relationships only fill a missing directed pair; accepted memory supplies
    provenance for an existing pair and never creates separate graph truth.
    """

    def snapshot(self, project: NovelProject) -> RelationshipGraphSnapshot:
        cards = tuple(self._read_card(path) for path in project.list_characters())
        state = project.load_story_state()
        characters = state.get("characters") if isinstance(state, dict) else {}
        if not isinstance(characters, dict):
            characters = {}

        aliases: dict[str, str] = {}
        card_by_name: dict[str, _Card] = {}
        for card in cards:
            card_by_name[card.name] = card
            for value in (card.name, card.path.stem, *card.aliases):
                key = _identity_key(value)
                if key:
                    aliases.setdefault(key, card.name)

        def canonical(raw: object) -> str:
            value = str(raw or "").strip()
            return aliases.get(_identity_key(value), value)

        node_details: dict[str, dict[str, Any]] = {}
        for card in cards:
            node_details[card.name] = {
                "card": card,
                "state": "",
                "location": "",
            }
        for raw_name, raw_details in characters.items():
            name = canonical(raw_name)
            if not name:
                continue
            details = raw_details if isinstance(raw_details, dict) else {}
            node = node_details.setdefault(
                name, {"card": card_by_name.get(name), "state": "", "location": ""}
            )
            node["state"] = str(details.get("state") or "").strip()
            node["location"] = str(details.get("location") or "").strip()

        relations: dict[tuple[str, str], tuple[str, str]] = {}
        for raw_source, raw_details in characters.items():
            source = canonical(raw_source)
            if not source or not isinstance(raw_details, dict):
                continue
            raw_relations = raw_details.get("relations")
            if not isinstance(raw_relations, dict):
                continue
            for raw_target, raw_label in raw_relations.items():
                target, label = canonical(raw_target), str(raw_label or "").strip()
                if source and target and label:
                    relations[(source, target)] = (label, "story_state")

        for card in cards:
            for raw_target, label in card.relations:
                target = canonical(raw_target)
                if target and label:
                    relations.setdefault((card.name, target), (label, "character_card"))

        for source, target in relations:
            node_details.setdefault(
                source, {"card": card_by_name.get(source), "state": "", "location": ""}
            )
            node_details.setdefault(
                target, {"card": card_by_name.get(target), "state": "", "location": ""}
            )

        node_ids = {name: _stable_id("character", name) for name in node_details}
        evidence_by_pair, memory_warning = self._relationship_evidence(
            project, relations, canonical
        )
        warnings: list[GraphWarning] = []
        if memory_warning:
            warnings.append(
                GraphWarning(
                    "accepted_memory_invalid",
                    "已采用记忆记录无法验证，关系证据已安全忽略。",
                    "",
                    "",
                )
            )

        nodes: list[GraphNode] = []
        for name in sorted(node_details, key=_sort_key):
            details = node_details[name]
            card = details.get("card")
            resolved = isinstance(card, _Card)
            if not resolved:
                warnings.append(
                    GraphWarning(
                        "missing_character_card",
                        f"“{name}”出现在故事关系中，但尚未建立人物卡。",
                        name,
                        "",
                    )
                )
            nodes.append(
                GraphNode(
                    node_id=node_ids[name],
                    name=name,
                    aliases=card.aliases if resolved else (),
                    state=str(details.get("state") or ""),
                    location=str(details.get("location") or ""),
                    resolved=resolved,
                    path=str(card.path.resolve()) if resolved else None,
                    relative_path=(
                        card.path.resolve().relative_to(project.root).as_posix()
                        if resolved
                        else None
                    ),
                )
            )

        edges = tuple(
            GraphEdge(
                edge_id=_stable_id("relation", f"{source}\0{target}"),
                source=node_ids[source],
                target=node_ids[target],
                label=value[0],
                source_kind=value[1],
                evidence=evidence_by_pair.get((source, target), ()),
            )
            for (source, target), value in sorted(
                relations.items(), key=lambda item: (_sort_key(item[0][0]), _sort_key(item[0][1]))
            )
        )
        relation_types = tuple(sorted({edge.label for edge in edges}, key=_sort_key))
        return RelationshipGraphSnapshot(
            project_name=project.name,
            revision=self._revision(project, cards),
            nodes=tuple(nodes),
            edges=edges,
            warnings=tuple(warnings),
            relation_types=relation_types,
        )

    @staticmethod
    def _read_card(path: Path) -> _Card:
        text = path.read_text(encoding="utf-8")
        heading = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        name = heading.group(1).strip() if heading else path.stem
        aliases_match = re.search(r"(?m)^-\s*别名[：:]\s*(.+?)\s*$", text)
        aliases = _split_aliases(aliases_match.group(1)) if aliases_match else ()
        section = re.search(r"(?ms)^##\s+关键关系\s*$\n(.*?)(?=^##\s|\Z)", text)
        relations: dict[str, str] = {}
        if section:
            for target, label in re.findall(
                r"(?m)^-\s*([^：:\n]+)[：:]\s*(.+?)\s*$", section.group(1)
            ):
                clean_target, clean_label = target.strip(), label.strip().rstrip("。")
                if clean_target and clean_label and clean_target != "无":
                    relations[clean_target] = clean_label
        managed = re.search(r"(?m)^-\s*当前关系[：:]\s*(.+?)\s*$", text)
        if managed:
            for item in re.split(r"[；;]+", managed.group(1)):
                pair = re.split(r"[：:]", item, maxsplit=1)
                if len(pair) != 2:
                    continue
                target, label = pair[0].strip(), pair[1].strip().rstrip("。")
                if target and label and target != "无":
                    relations[target] = label
        return _Card(name, aliases, path.resolve(), tuple(relations.items()))

    @staticmethod
    def _relationship_evidence(
        project: NovelProject,
        relations: dict[tuple[str, str], tuple[str, str]],
        canonical,
    ) -> tuple[dict[tuple[str, str], tuple[GraphEvidence, ...]], bool]:
        view = AcceptedMemoryView(project, project.load_chapter_summaries())
        if view.error:
            return {}, True
        gathered: dict[tuple[str, str], list[GraphEvidence]] = {}
        for chapter_id, record in view.records.items():
            if view.status(chapter_id) != "verified" or not isinstance(record, dict):
                continue
            facts = record.get("evidence_facts")
            if not isinstance(facts, list):
                continue
            for fact in facts:
                if not isinstance(fact, dict) or fact.get("category") != "relationship":
                    continue
                subject = canonical(fact.get("subject"))
                predicate = str(fact.get("predicate") or "").strip()
                value = str(fact.get("value") or "").strip()
                searchable = f"{predicate} {value}".casefold()
                for (source, target), (label, _source_kind) in relations.items():
                    if subject != source:
                        continue
                    if target.casefold() not in searchable and label.casefold() not in searchable:
                        continue
                    gathered.setdefault((source, target), []).append(
                        GraphEvidence(
                            chapter_id=str(chapter_id),
                            anchor=str(fact.get("anchor") or ""),
                            description=" ".join(part for part in (subject, predicate, value) if part),
                            certainty=str(fact.get("certainty") or "uncertain"),
                        )
                    )
        return {key: tuple(value) for key, value in gathered.items()}, False

    @staticmethod
    def _revision(project: NovelProject, cards: tuple[_Card, ...]) -> str:
        paths = [
            project.memory_dir / "story_state.json",
            project.memory_dir / "chapter_summaries.json",
            project.memory_dir / "accepted_chapter_memory.json",
            *(card.path for card in cards),
        ]
        digest = hashlib.sha256()
        for path in sorted(paths, key=lambda item: item.as_posix().casefold()):
            if not path.is_file():
                continue
            relative = path.resolve().relative_to(project.root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return "graph-v1:" + digest.hexdigest()


def _identity_key(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _split_aliases(value: str) -> tuple[str, ...]:
    return tuple(
        part.strip()
        for part in re.split(r"[、,，/]+", value)
        if part.strip() and part.strip() != "无"
    )


def _sort_key(value: str) -> tuple[str, str]:
    return value.casefold(), value


def _stable_id(namespace: str, value: str) -> str:
    value_hash = hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()[:16]
    return f"{namespace}:{value_hash}"
