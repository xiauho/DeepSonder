"""Review-first reconstruction of schema-v2 knowledge from manuscript evidence."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.project_v2_schema import ProjectV2Descriptor
from core.storage import atomic_write_text

from .document_v2_service import DocumentV2Service
from .structured_extraction_service import StructuredExtractionService


class ReconstructionError(ValueError):
    """Raised when a proposal or review transition is invalid."""


class ReconstructionCancelled(Exception):
    """Raised cooperatively before any proposal batch is committed."""


@dataclass(frozen=True)
class ReconstructionSnapshot:
    source_revision: str
    accepted_entity_count: int
    accepted_relation_count: int
    accepted_world_count: int
    accepted_character_field_count: int
    accepted_event_count: int
    pending_count: int
    stale_batch_count: int
    batches: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class KnowledgeSnapshot:
    entities: tuple[dict[str, Any], ...]
    relations: tuple[dict[str, Any], ...]
    worlds: tuple[dict[str, Any], ...]
    hidden_worlds: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    hidden_events: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    diagnostics: tuple[dict[str, Any], ...]
    merges: tuple[dict[str, Any], ...]
    operation_count: int


_NAME = r"[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·]{0,19}"
_EXPLICIT_ENTITY = re.compile(rf"\[\[人物[：:]\s*(?P<name>{_NAME})\s*\]\]")
_EXPLICIT_RELATION = re.compile(
    rf"\[\[关系[：:]\s*(?P<source>{_NAME})\s*\|\s*(?P<label>[^|\]\n]{{1,30}})\s*\|\s*(?P<target>{_NAME})\s*\]\]"
)
_EXPLICIT_WORLD = re.compile(
    rf"\[\[世界[：:]\s*(?P<name>{_NAME})\s*\|\s*(?P<category>[^|\]\n]{{1,30}})\s*\|\s*(?P<description>[^|\]\n]{{1,120}})\s*\]\]"
)
_EXPLICIT_CHARACTER_FIELD = re.compile(
    rf"\[\[角色字段[：:]\s*(?P<character>{_NAME})\s*\|\s*(?P<field>[^|\]\n]{{1,20}})\s*\|\s*(?P<value>[^|\]\n]{{1,120}})\s*\]\]"
)
_EXPLICIT_EVENT = re.compile(
    r"\[\[事件[：:]\s*(?P<time>[^|\]\n]{1,40})\s*\|\s*(?P<title>[^|\]\n]{1,80})\s*\|\s*"
    r"(?P<description>[^|\]\n]{1,120})\s*\|\s*(?P<characters>[^|\]\n]{1,120})\s*\|\s*"
    r"(?P<worlds>[^|\]\n]{1,120})\s*\]\]"
)
_CHARACTER_FIELDS = {"身份", "外貌", "性格", "目标", "能力", "阵营", "状态"}
_ACTION_ENTITY = re.compile(
    r"(?<![\u4e00-\u9fff])(?P<name>[\u4e00-\u9fff]{2,4}?)"
    r"(?=(?:低声|轻声|沉声|忽然|冷冷)?(?:说|问|答|喊|道|笑道|说道|示意|看向|望向|走向|拆开|拿起|递给|抓住|推开|检查|后退|离开))"
)
_PAIR = re.compile(
    r"(?<![\u4e00-\u9fff])(?P<source>[\u4e00-\u9fff]{2,4})[与和]"
    r"(?P<target>[\u4e00-\u9fff]{2,4})(?=(?:一同|一起|共同|沿|前往|来到|走向|调查|离开))"
)
_NATURAL_RELATION = re.compile(
    r"(?<![\u4e00-\u9fff])(?P<source>[\u4e00-\u9fff]{2,4})是"
    r"(?P<target>[\u4e00-\u9fff]{2,4})的"
    r"(?P<label>朋友|搭档|同伴|父亲|母亲|兄长|弟弟|姐姐|妹妹|老师|学生|上司|下属|敌人|盟友|恋人)"
)
_STOP_NAMES = {
    "他们", "她们", "我们", "你们", "自己", "自己先", "两人", "众人", "对方", "那人",
    "低声", "轻声", "沉声", "冷冷", "忽然",
}


class ReconstructionService:
    """Generate deterministic proposals and materialize only reviewed truth."""

    def __init__(self, structured_extractor: StructuredExtractionService | None = None) -> None:
        self.structured_extractor = structured_extractor or StructuredExtractionService()

    def snapshot(self, project: ProjectV2Descriptor) -> ReconstructionSnapshot:
        index = self._read_collection(project.root / "proposals" / "index.json", "batches")
        entities = self._read_collection(project.root / "knowledge" / "entities.json", "entities")
        relations = self._read_collection(project.root / "knowledge" / "relations.json", "relations")
        worlds = self._read_collection(project.root / "knowledge" / "world_rules.json", "rules")
        timeline = self._read_collection(project.root / "knowledge" / "timeline.json", "events")
        current = self._source_state(project)[0]
        batches = tuple(index["batches"])
        return ReconstructionSnapshot(
            source_revision=current,
            accepted_entity_count=len(entities["entities"]),
            accepted_relation_count=len(relations["relations"]),
            accepted_world_count=len(worlds["rules"]),
            accepted_character_field_count=sum(len(item.get("profile_fields", {})) for item in entities["entities"]),
            accepted_event_count=len(timeline["events"]),
            pending_count=sum(int(item.get("pending_count", 0)) for item in batches if item.get("status") != "stale"),
            stale_batch_count=sum(1 for item in batches if item.get("status") == "stale"),
            batches=batches,
        )

    def generate(
        self,
        project: ProjectV2Descriptor,
        *,
        extraction_mode: str = "local",
        remote_consent: bool = False,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        if extraction_mode not in {"local", "dsh"}:
            raise ReconstructionError("识别模式必须是 local 或 dsh。")
        if extraction_mode == "dsh" and not remote_consent:
            raise ReconstructionError("使用 DSH 增强识别前必须明确同意发送正文证据片段。")
        source_revision, chapters = self._source_state(project)
        cancelled = cancel_event or threading.Event()
        progress = progress_callback or (lambda _stage, _value: None)
        index_path = project.root / "proposals" / "index.json"
        index = self._read_collection(index_path, "batches")
        for summary in index["batches"]:
            summary_mode = summary.get("requested_mode", "local")
            if (summary.get("source_revision") == source_revision and summary.get("status") != "stale"
                    and summary_mode == extraction_mode and not summary.get("fallback_used", False)):
                self._checkpoint_path(project).unlink(missing_ok=True)
                return self.get_batch(project, str(summary["batch_id"]))

        self._mark_all_stale(project, index)
        checkpoint = self._read_checkpoint(project, source_revision)
        segments: list[dict[str, Any]] = list(checkpoint.get("segments", []))
        completed = set(checkpoint.get("completed_chapters", []))
        chapter_revisions: dict[str, str] = {}
        total = max(1, len(chapters))
        for position, (chapter_id, revision, content) in enumerate(chapters, start=1):
            chapter_revisions[chapter_id] = revision
            if chapter_id in completed:
                continue
            if cancelled.is_set():
                raise ReconstructionCancelled()
            progress(f"正在分析 {chapter_id}", 10 + int(position / total * 65))
            if cancelled.is_set():
                raise ReconstructionCancelled()
            segments.extend(self._segments(chapter_id, revision, content))
            completed.add(chapter_id)
            self._write_json(self._checkpoint_path(project), {
                "schema_version": 1, "source_revision": source_revision,
                "completed_chapters": sorted(completed), "segments": segments,
            })
        if cancelled.is_set():
            raise ReconstructionCancelled()
        progress("正在汇总候选", 82)
        local_proposals = self._extract_proposals(source_revision, segments)
        proposals = local_proposals
        extraction = {
            "requested_mode": extraction_mode,
            "producer": "local",
            "fallback_used": False,
            "fallback_reason": "",
            "segment_count": len(segments),
            "remote_chunk_count": 0,
            "estimated_input_tokens": 0,
            "privacy_scope": "local_only" if extraction_mode == "local" else "manuscript_evidence_segments_only",
            "duplicate_count": 0,
            "relation_conflict_count": self._relation_conflict_count(local_proposals),
        }
        if extraction_mode == "dsh":
            try:
                remote = self.structured_extractor.extract(
                    source_revision, segments, cancel_event=cancelled, progress_callback=progress,
                )
            except Exception:
                if cancelled.is_set():
                    raise ReconstructionCancelled() from None
                extraction["fallback_used"] = True
                extraction["fallback_reason"] = "remote_unavailable_or_invalid"
                progress("DSH 不可用，已回退本地识别", 94)
            else:
                extraction["duplicate_count"] = len(
                    {(item["kind"], item["identity_key"]) for item in local_proposals}
                    & {(item["kind"], item["identity_key"]) for item in remote.proposals}
                )
                proposals = self._combine_proposals(local_proposals, list(remote.proposals))
                extraction.update({
                    "producer": "local+dsh",
                    "segment_count": remote.segment_count,
                    "remote_chunk_count": remote.remote_chunk_count,
                    "estimated_input_tokens": remote.estimated_input_tokens,
                    "relation_conflict_count": self._relation_conflict_count(proposals),
                })
        batch_id = "batch_" + self._digest(source_revision + "\0" + self._now())[:20]
        for proposal in proposals:
            proposal["proposal_id"] = "proposal_" + self._digest(
                batch_id + "\0" + proposal["kind"] + "\0" + proposal["identity_key"]
            )[:20]
            proposal["status"] = "pending"
            proposal.pop("identity_key", None)
        batch = {
            "schema_version": 1,
            "batch_id": batch_id,
            "source_revision": source_revision,
            "created_at": self._now(),
            "status": "pending" if proposals else "reviewed",
            "chapter_revisions": chapter_revisions,
            "segments": segments,
            "proposals": proposals,
            "extraction": extraction,
        }
        batch_path = project.root / "proposals" / f"{batch_id}.json"
        self._write_json(batch_path, batch)
        index["batches"].append(self._summary(batch))
        self._write_json(index_path, index)
        self._rebuild_knowledge(project)
        self._checkpoint_path(project).unlink(missing_ok=True)
        progress("候选已生成", 100)
        return batch

    def get_batch(self, project: ProjectV2Descriptor, batch_id: str) -> dict[str, Any]:
        if re.fullmatch(r"batch_[0-9a-f]{20}", str(batch_id or "")) is None:
            raise ReconstructionError("候选批次 ID 无效。")
        path = project.root / "proposals" / f"{batch_id}.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReconstructionError("候选批次不存在或无法读取。") from exc
        if not isinstance(value, dict) or value.get("batch_id") != batch_id:
            raise ReconstructionError("候选批次格式无效。")
        return value

    def review(
        self,
        project: ProjectV2Descriptor,
        batch_id: str,
        proposal_id: str,
        decision: str,
    ) -> dict[str, Any]:
        return self.review_many(project, batch_id, [{"proposal_id": proposal_id, "decision": decision}])

    def review_many(
        self,
        project: ProjectV2Descriptor,
        batch_id: str,
        decisions: list[dict[str, str]],
    ) -> dict[str, Any]:
        if not 1 <= len(decisions) <= 200:
            raise ReconstructionError("一次批量审核必须包含 1 至 200 个候选。")
        batch = self.get_batch(project, batch_id)
        if batch.get("status") == "stale":
            raise ReconstructionError("正文已经变化，该候选批次已失效。")
        by_id = {str(item.get("proposal_id")): item for item in batch.get("proposals", [])}
        selected: list[tuple[dict[str, Any], str]] = []
        seen: set[str] = set()
        for item in decisions:
            if not isinstance(item, dict) or set(item) != {"proposal_id", "decision"}:
                raise ReconstructionError("批量审核项格式无效。")
            proposal_id, decision = str(item["proposal_id"]), str(item["decision"])
            if decision not in {"accepted", "rejected"}:
                raise ReconstructionError("审核决定必须是 accepted 或 rejected。")
            if proposal_id in seen:
                raise ReconstructionError("批量审核不能重复包含同一候选。")
            seen.add(proposal_id)
            proposal = by_id.get(proposal_id)
            if proposal is None:
                raise ReconstructionError("候选项不存在。")
            if proposal.get("status") != "pending":
                raise ReconstructionError("候选项已经审核。")
            selected.append((proposal, decision))
        accepted = [*self._accepted_proposals(project, excluding_batch=batch_id), *(
            item for item in batch["proposals"] if item.get("status") == "accepted"
        ), *(proposal for proposal, decision in selected if decision == "accepted")]
        accepted_names = {str(item.get("name")) for item in accepted if item.get("kind") == "entity"}
        world_name_counts: dict[str, int] = {}
        for item in accepted:
            if item.get("kind") == "world":
                key = str(item.get("name", "")).casefold()
                world_name_counts[key] = world_name_counts.get(key, 0) + 1
        for proposal, decision in selected:
            if decision != "accepted" or proposal.get("kind") not in {"relation", "character_field", "event"}:
                continue
            if proposal.get("kind") == "relation":
                dependencies = (proposal.get("source_name"), proposal.get("target_name"))
            elif proposal.get("kind") == "character_field":
                dependencies = (proposal.get("character_name"),)
            else:
                dependencies = tuple(proposal.get("character_names", []))
            missing = [name for name in dependencies if name not in accepted_names]
            if missing:
                raise ReconstructionError("请同时接受所需人物候选：" + "、".join(str(item) for item in missing))
            if proposal.get("kind") == "event":
                invalid_worlds = [str(name) for name in proposal.get("world_names", []) if world_name_counts.get(str(name).casefold(), 0) != 1]
                if invalid_worlds:
                    raise ReconstructionError("事件引用的世界观缺失或名称不唯一：" + "、".join(invalid_worlds))
        profile_values: dict[tuple[str, str], str] = {}
        world_values: dict[tuple[str, str], str] = {}
        event_values: dict[tuple[str, str], str] = {}
        for proposal in accepted:
            if proposal.get("kind") == "character_field":
                key = (str(proposal.get("character_name", "")).casefold(), str(proposal.get("field", "")).casefold())
                value = str(proposal.get("value", "")).casefold()
                if key in profile_values and profile_values[key] != value:
                    raise ReconstructionError("同一人物字段存在冲突，请只接受一个版本。")
                profile_values[key] = value
            elif proposal.get("kind") == "world":
                key = (str(proposal.get("name", "")).casefold(), str(proposal.get("category", "")).casefold())
                value = str(proposal.get("description", "")).casefold()
                if key in world_values and world_values[key] != value:
                    raise ReconstructionError("同一世界观条目存在冲突，请只接受一个版本。")
                world_values[key] = value
            elif proposal.get("kind") == "event":
                key = (str(proposal.get("time_label", "")).casefold(), str(proposal.get("title", "")).casefold())
                value = json.dumps({
                    "description": proposal.get("description", ""),
                    "character_names": proposal.get("character_names", []),
                    "world_names": proposal.get("world_names", []),
                }, ensure_ascii=False, sort_keys=True)
                if key in event_values and event_values[key] != value:
                    raise ReconstructionError("同一时间和标题的事件存在冲突，请只接受一个版本。")
                event_values[key] = value
        reviewed_at = self._now()
        review_mode = "batch" if len(selected) > 1 else "single"
        for proposal, decision in selected:
            proposal["status"] = decision
            proposal["reviewed_at"] = reviewed_at
            proposal["review_mode"] = review_mode
        pending = sum(1 for item in batch["proposals"] if item.get("status") == "pending")
        batch["status"] = "pending" if pending else "reviewed"
        self._write_json(project.root / "proposals" / f"{batch_id}.json", batch)
        index = self._read_collection(project.root / "proposals" / "index.json", "batches")
        index["batches"] = [self._summary(batch) if item.get("batch_id") == batch_id else item for item in index["batches"]]
        self._write_json(project.root / "proposals" / "index.json", index)
        self._rebuild_knowledge(project)
        return batch

    def invalidate_chapter(self, project: ProjectV2Descriptor, chapter_id: str) -> bool:
        index_path = project.root / "proposals" / "index.json"
        index = self._read_collection(index_path, "batches")
        changed = False
        for summary in index["batches"]:
            if summary.get("status") == "stale":
                continue
            batch = self.get_batch(project, str(summary.get("batch_id")))
            if chapter_id not in batch.get("chapter_revisions", {}):
                continue
            batch["status"] = "stale"
            batch["stale_at"] = self._now()
            self._write_json(project.root / "proposals" / f"{batch['batch_id']}.json", batch)
            summary.update(self._summary(batch))
            changed = True
        if changed:
            self._write_json(index_path, index)
            self._rebuild_knowledge(project)
        return changed

    def graph_snapshot(self, project: ProjectV2Descriptor):
        from .relationship_graph_service import (
            GraphEdge, GraphEvidence, GraphNode, GraphWarning, RelationshipGraphSnapshot,
        )

        entities = self._read_collection(project.root / "knowledge" / "entities.json", "entities")["entities"]
        relations = self._read_collection(project.root / "knowledge" / "relations.json", "relations")["relations"]
        worlds = self._read_collection(project.root / "knowledge" / "world_rules.json", "rules")["rules"]
        timeline = self._read_collection(project.root / "knowledge" / "timeline.json", "events")
        events = timeline["events"]
        evidence = self._read_collection(project.root / "provenance" / "evidence.json", "evidence")["evidence"]
        evidence_by_id = {item["evidence_id"]: item for item in evidence}
        character_nodes = tuple(GraphNode(
            node_id=item["entity_id"], name=item["display_name"], aliases=tuple(item.get("aliases", [])),
            state="", location="", resolved=True, path=None, relative_path=None, node_kind="character",
        ) for item in entities)
        world_nodes = tuple(GraphNode(
            node_id=item["world_id"], name=item["name"], aliases=(),
            state=item["category"], location=item["description"], resolved=True,
            path=None, relative_path=None, node_kind="world",
        ) for item in worlds)
        event_nodes = tuple(GraphNode(
            node_id=item["event_id"], name=item["title"], aliases=(),
            state=item["time_label"], location=item["description"], resolved=True,
            path=None, relative_path=None, node_kind="event", order=int(item.get("order", 0)),
        ) for item in events)

        def graph_evidence(ids: list[str]) -> tuple[GraphEvidence, ...]:
            return tuple(GraphEvidence(
                chapter_id=evidence_by_id[eid]["chapter_id"], anchor=evidence_by_id[eid]["anchor"],
                description=evidence_by_id[eid]["text"], certainty="reviewed",
            ) for eid in ids if eid in evidence_by_id)

        relation_edges = tuple(GraphEdge(
            edge_id=item["relation_id"], source=item["source_entity_id"], target=item["target_entity_id"],
            label=item["label"], source_kind="reviewed_v2",
            evidence=graph_evidence(item.get("evidence_ids", [])), edge_kind="relationship",
        ) for item in relations)
        semantic_edges: list[GraphEdge] = []
        for event in events:
            refs = graph_evidence(event.get("evidence_ids", []))
            for entity_id in event.get("participant_entity_ids", []):
                semantic_edges.append(GraphEdge(
                    edge_id="link_" + self._digest(f"{entity_id}\0{event['event_id']}\0participation")[:20],
                    source=entity_id, target=event["event_id"], label="参与事件",
                    source_kind="reviewed_v2", evidence=refs, edge_kind="participation",
                ))
            for world_id in event.get("world_ids", []):
                semantic_edges.append(GraphEdge(
                    edge_id="link_" + self._digest(f"{event['event_id']}\0{world_id}\0setting")[:20],
                    source=event["event_id"], target=world_id, label="发生于",
                    source_kind="reviewed_v2", evidence=refs, edge_kind="setting",
                ))
        nodes = (*character_nodes, *world_nodes, *event_nodes)
        edges = (*relation_edges, *semantic_edges)
        warnings = tuple(GraphWarning(
            code=str(item.get("code", "knowledge_diagnostic")),
            message=str(item.get("message", "知识投影存在待确认问题。")),
            subject=str((item.get("event_ids") or [""])[0]),
            target=str((item.get("event_ids") or ["", ""])[1]) if len(item.get("event_ids") or []) > 1 else "",
        ) for item in timeline.get("diagnostics", []))
        revision = self._digest(json.dumps(
            [entities, relations, worlds, events, timeline.get("diagnostics", [])],
            ensure_ascii=False, sort_keys=True,
        ))
        return RelationshipGraphSnapshot(
            project.name, revision, nodes, edges, warnings,
            tuple(sorted({item.label for item in edges})),
        )

    def knowledge_snapshot(self, project: ProjectV2Descriptor) -> KnowledgeSnapshot:
        entities = self._read_collection(project.root / "knowledge" / "entities.json", "entities")["entities"]
        relations = self._read_collection(project.root / "knowledge" / "relations.json", "relations")["relations"]
        world_collection = self._read_collection(project.root / "knowledge" / "world_rules.json", "rules")
        worlds = world_collection["rules"]
        hidden_worlds = world_collection.get("hidden_rules", [])
        timeline_collection = self._read_collection(project.root / "knowledge" / "timeline.json", "events")
        events = timeline_collection["events"]
        hidden_events = timeline_collection.get("hidden_events", [])
        diagnostics = timeline_collection.get("diagnostics", [])
        evidence = self._read_collection(project.root / "provenance" / "evidence.json", "evidence")["evidence"]
        curation = self._read_curation(project)
        entity_names = {item["entity_id"]: item["display_name"] for item in entities}
        historical_names: dict[str, str] = {}
        for operation in curation["operations"]:
            subjects = operation.get("subject_ids", [])
            before = operation.get("before", {})
            if operation.get("kind") == "entity.merge" and len(subjects) == 2 and isinstance(before, dict):
                historical_names[str(subjects[0])] = str(before.get("source", subjects[0]))
                historical_names[str(subjects[1])] = str(before.get("target", subjects[1]))
        merges = tuple({
            "source_entity_id": source_id,
            "target_entity_id": target_id,
            "source_name": historical_names.get(source_id, source_id),
            "target_name": entity_names.get(self._resolve_merge(curation["entity_merges"], target_id), historical_names.get(target_id, target_id)),
        } for source_id, target_id in sorted(curation["entity_merges"].items()) if isinstance(target_id, str))
        return KnowledgeSnapshot(
            tuple(entities), tuple(relations), tuple(worlds), tuple(hidden_worlds),
            tuple(events), tuple(hidden_events), tuple(evidence), tuple(diagnostics),
            merges, len(curation["operations"]),
        )

    def rename_entity(self, project: ProjectV2Descriptor, entity_id: str, display_name: str) -> KnowledgeSnapshot:
        entity = self._knowledge_entity(project, entity_id)
        name = self._clean_label(display_name, "人物名称", 80)
        self._assert_identity_available(project, name, excluding=entity_id)
        curation = self._read_curation(project)
        override = curation["entity_overrides"].setdefault(entity_id, {})
        before = {"display_name": entity["display_name"]}
        override["display_name"] = name
        self._record(curation, "entity.rename", [entity_id], before, {"display_name": name})
        return self._commit_curation(project, curation)

    def set_entity_aliases(
        self, project: ProjectV2Descriptor, entity_id: str, aliases: list[str]
    ) -> KnowledgeSnapshot:
        entity = self._knowledge_entity(project, entity_id)
        cleaned: list[str] = []
        for value in aliases:
            alias = self._clean_label(value, "人物别名", 80)
            if alias.casefold() == str(entity["display_name"]).casefold() or alias.casefold() in {item.casefold() for item in cleaned}:
                continue
            self._assert_identity_available(project, alias, excluding=entity_id)
            cleaned.append(alias)
        if len(cleaned) > 20:
            raise ReconstructionError("单个人物最多允许 20 个别名。")
        curation = self._read_curation(project)
        override = curation["entity_overrides"].setdefault(entity_id, {})
        before = {"aliases": list(entity.get("aliases", []))}
        override["aliases"] = cleaned
        self._record(curation, "entity.aliases", [entity_id], before, {"aliases": cleaned})
        return self._commit_curation(project, curation)

    def merge_entities(
        self, project: ProjectV2Descriptor, source_entity_id: str, target_entity_id: str
    ) -> KnowledgeSnapshot:
        source = self._knowledge_entity(project, source_entity_id)
        target = self._knowledge_entity(project, target_entity_id)
        if source_entity_id == target_entity_id:
            raise ReconstructionError("不能把人物合并到自身。")
        curation = self._read_curation(project)
        if self._resolve_merge(curation["entity_merges"], target_entity_id) == source_entity_id:
            raise ReconstructionError("人物合并会形成循环。")
        curation["entity_merges"][source_entity_id] = target_entity_id
        self._record(
            curation, "entity.merge", [source_entity_id, target_entity_id],
            {"source": source["display_name"], "target": target["display_name"]},
            {"merged_into": target_entity_id},
        )
        return self._commit_curation(project, curation)

    def unmerge_entity(self, project: ProjectV2Descriptor, source_entity_id: str) -> KnowledgeSnapshot:
        curation = self._read_curation(project)
        target_entity_id = curation["entity_merges"].get(source_entity_id)
        if not isinstance(target_entity_id, str):
            raise ReconstructionError("该人物没有可撤销的合并记录。")
        del curation["entity_merges"][source_entity_id]
        self._record(
            curation, "entity.unmerge", [source_entity_id, target_entity_id],
            {"merged_into": target_entity_id}, {"merged_into": None},
        )
        return self._commit_curation(project, curation)

    def update_relation(
        self,
        project: ProjectV2Descriptor,
        relation_id: str,
        source_entity_id: str,
        target_entity_id: str,
        label: str,
    ) -> KnowledgeSnapshot:
        relation = self._knowledge_relation(project, relation_id)
        self._knowledge_entity(project, source_entity_id)
        self._knowledge_entity(project, target_entity_id)
        if source_entity_id == target_entity_id:
            raise ReconstructionError("关系两端不能是同一人物。")
        clean_label = self._clean_label(label, "关系名称", 80)
        curation = self._read_curation(project)
        after = {
            "source_entity_id": source_entity_id,
            "target_entity_id": target_entity_id,
            "label": clean_label,
            "deleted": False,
        }
        curation["relation_overrides"][relation_id] = after
        self._record(curation, "relation.update", [relation_id], {
            "source_entity_id": relation["source_entity_id"],
            "target_entity_id": relation["target_entity_id"], "label": relation["label"],
        }, after)
        return self._commit_curation(project, curation)

    def delete_relation(self, project: ProjectV2Descriptor, relation_id: str) -> KnowledgeSnapshot:
        relation = self._knowledge_relation(project, relation_id)
        curation = self._read_curation(project)
        override = curation["relation_overrides"].setdefault(relation_id, {})
        override["deleted"] = True
        self._record(curation, "relation.delete", [relation_id], relation, {"deleted": True})
        return self._commit_curation(project, curation)

    def update_character_field(
        self, project: ProjectV2Descriptor, entity_id: str, field: str, value: str
    ) -> KnowledgeSnapshot:
        entity = self._knowledge_entity(project, entity_id)
        clean_field = self._clean_label(field, "角色字段", 20)
        if clean_field not in _CHARACTER_FIELDS or clean_field not in entity.get("profile_fields", {}):
            raise ReconstructionError("角色字段不存在、已隐藏或不在允许范围内。")
        clean_value = self._clean_label(value, "角色字段内容", 200)
        before = {"field": clean_field, "value": entity["profile_fields"][clean_field], "hidden": False}
        curation = self._read_curation(project)
        override = curation["character_field_overrides"].setdefault(entity_id, {}).setdefault(clean_field, {})
        override.update({"value": clean_value, "hidden": False})
        self._record(curation, "character_field.update", [entity_id, clean_field], before, {"field": clean_field, "value": clean_value, "hidden": False})
        return self._commit_curation(project, curation)

    def hide_character_field(
        self, project: ProjectV2Descriptor, entity_id: str, field: str
    ) -> KnowledgeSnapshot:
        entity = self._knowledge_entity(project, entity_id)
        clean_field = self._clean_label(field, "角色字段", 20)
        if clean_field not in entity.get("profile_fields", {}):
            raise ReconstructionError("角色字段不存在或已经隐藏。")
        value = str(entity["profile_fields"][clean_field])
        curation = self._read_curation(project)
        override = curation["character_field_overrides"].setdefault(entity_id, {}).setdefault(clean_field, {})
        override.update({"value": value, "hidden": True})
        self._record(curation, "character_field.hide", [entity_id, clean_field], {"value": value, "hidden": False}, {"value": value, "hidden": True})
        return self._commit_curation(project, curation)

    def restore_character_field(
        self, project: ProjectV2Descriptor, entity_id: str, field: str
    ) -> KnowledgeSnapshot:
        entity = self._knowledge_entity(project, entity_id)
        clean_field = self._clean_label(field, "角色字段", 20)
        if clean_field not in entity.get("hidden_profile_fields", {}):
            raise ReconstructionError("角色字段没有可恢复的隐藏记录。")
        value = str(entity["hidden_profile_fields"][clean_field])
        curation = self._read_curation(project)
        override = curation["character_field_overrides"].setdefault(entity_id, {}).setdefault(clean_field, {})
        override.update({"value": value, "hidden": False})
        self._record(curation, "character_field.restore", [entity_id, clean_field], {"value": value, "hidden": True}, {"value": value, "hidden": False})
        return self._commit_curation(project, curation)

    def update_world(
        self, project: ProjectV2Descriptor, world_id: str, name: str, category: str, description: str
    ) -> KnowledgeSnapshot:
        world = self._knowledge_world(project, world_id)
        clean_name = self._clean_label(name, "世界观名称", 80)
        clean_category = self._clean_label(category, "世界观类型", 80)
        clean_description = self._clean_label(description, "世界观描述", 200)
        if any(
            item["world_id"] != world_id and str(item["name"]).casefold() == clean_name.casefold()
            and str(item["category"]).casefold() == clean_category.casefold()
            for item in self.knowledge_snapshot(project).worlds
        ):
            raise ReconstructionError("相同名称和类型的世界观条目已经存在。")
        after = {"name": clean_name, "category": clean_category, "description": clean_description, "hidden": False}
        curation = self._read_curation(project)
        curation["world_overrides"].setdefault(world_id, {}).update(after)
        self._record(curation, "world.update", [world_id], {
            "name": world["name"], "category": world["category"], "description": world["description"], "hidden": False,
        }, after)
        return self._commit_curation(project, curation)

    def hide_world(self, project: ProjectV2Descriptor, world_id: str) -> KnowledgeSnapshot:
        world = self._knowledge_world(project, world_id)
        curation = self._read_curation(project)
        override = curation["world_overrides"].setdefault(world_id, {})
        override.update({"name": world["name"], "category": world["category"], "description": world["description"], "hidden": True})
        self._record(curation, "world.hide", [world_id], {"hidden": False}, {"hidden": True})
        return self._commit_curation(project, curation)

    def restore_world(self, project: ProjectV2Descriptor, world_id: str) -> KnowledgeSnapshot:
        world = self._knowledge_world(project, world_id, include_hidden=True)
        snapshot = self.knowledge_snapshot(project)
        if not any(item["world_id"] == world_id for item in snapshot.hidden_worlds):
            raise ReconstructionError("世界观条目没有可恢复的隐藏记录。")
        if any(
            str(item["name"]).casefold() == str(world["name"]).casefold()
            and str(item["category"]).casefold() == str(world["category"]).casefold()
            for item in snapshot.worlds
        ):
            raise ReconstructionError("恢复后会与现有世界观条目冲突。")
        curation = self._read_curation(project)
        curation["world_overrides"].setdefault(world_id, {})["hidden"] = False
        self._record(curation, "world.restore", [world_id], {"hidden": True}, {"hidden": False})
        return self._commit_curation(project, curation)

    def update_event(
        self, project: ProjectV2Descriptor, event_id: str, time_label: str, title: str, description: str
    ) -> KnowledgeSnapshot:
        event = self._knowledge_event(project, event_id)
        after = {
            "time_label": self._clean_label(time_label, "事件时间", 40),
            "title": self._clean_label(title, "事件标题", 80),
            "description": self._clean_label(description, "事件描述", 200),
            "hidden": False,
        }
        if any(
            item["event_id"] != event_id and str(item["time_label"]).casefold() == after["time_label"].casefold()
            and str(item["title"]).casefold() == after["title"].casefold()
            for item in self.knowledge_snapshot(project).events
        ):
            raise ReconstructionError("相同时间和标题的事件已经存在。")
        curation = self._read_curation(project)
        curation["event_overrides"].setdefault(event_id, {}).update(after)
        self._record(curation, "event.update", [event_id], {
            "time_label": event["time_label"], "title": event["title"],
            "description": event["description"], "hidden": False,
        }, after)
        return self._commit_curation(project, curation)

    def hide_event(self, project: ProjectV2Descriptor, event_id: str) -> KnowledgeSnapshot:
        event = self._knowledge_event(project, event_id)
        curation = self._read_curation(project)
        curation["event_overrides"].setdefault(event_id, {}).update({
            "time_label": event["time_label"], "title": event["title"],
            "description": event["description"], "hidden": True,
        })
        self._record(curation, "event.hide", [event_id], {"hidden": False}, {"hidden": True})
        return self._commit_curation(project, curation)

    def restore_event(self, project: ProjectV2Descriptor, event_id: str) -> KnowledgeSnapshot:
        event = self._knowledge_event(project, event_id, include_hidden=True)
        snapshot = self.knowledge_snapshot(project)
        if not any(item["event_id"] == event_id for item in snapshot.hidden_events):
            raise ReconstructionError("事件没有可恢复的隐藏记录。")
        if any(
            str(item["time_label"]).casefold() == str(event["time_label"]).casefold()
            and str(item["title"]).casefold() == str(event["title"]).casefold()
            for item in snapshot.events
        ):
            raise ReconstructionError("恢复后会与现有事件冲突。")
        curation = self._read_curation(project)
        curation["event_overrides"].setdefault(event_id, {})["hidden"] = False
        self._record(curation, "event.restore", [event_id], {"hidden": True}, {"hidden": False})
        return self._commit_curation(project, curation)

    def update_event_links(
        self,
        project: ProjectV2Descriptor,
        event_id: str,
        participant_entity_ids: list[str],
        world_ids: list[str],
    ) -> KnowledgeSnapshot:
        event = self._knowledge_event(project, event_id)
        if (
            not all(isinstance(item, str) for item in participant_entity_ids)
            or not all(isinstance(item, str) for item in world_ids)
            or len(participant_entity_ids) > 200 or len(world_ids) > 100
        ):
            raise ReconstructionError("事件语义连接必须是有界字符串 ID 数组。")
        if len(set(participant_entity_ids)) != len(participant_entity_ids) or len(set(world_ids)) != len(world_ids):
            raise ReconstructionError("事件语义连接不能重复。")
        for entity_id in participant_entity_ids:
            self._knowledge_entity(project, entity_id)
        for world_id in world_ids:
            self._knowledge_world(project, world_id)
        after = {
            "participant_entity_ids": sorted(participant_entity_ids),
            "world_ids": sorted(world_ids),
        }
        curation = self._read_curation(project)
        curation["event_overrides"].setdefault(event_id, {}).update(after)
        self._record(curation, "event.links.update", [event_id], {
            "participant_entity_ids": list(event.get("participant_entity_ids", [])),
            "world_ids": list(event.get("world_ids", [])),
        }, after)
        return self._commit_curation(project, curation)

    def reorder_events(
        self, project: ProjectV2Descriptor, event_ids: list[str]
    ) -> KnowledgeSnapshot:
        snapshot = self.knowledge_snapshot(project)
        active_ids = [str(item["event_id"]) for item in snapshot.events]
        if (
            not all(isinstance(item, str) for item in event_ids)
            or len(event_ids) > 1000 or len(set(event_ids)) != len(event_ids)
            or set(event_ids) != set(active_ids)
        ):
            raise ReconstructionError("事件排序必须完整且每个活动事件只能出现一次。")
        all_events = [*snapshot.events, *snapshot.hidden_events]
        available = {str(item["event_id"]) for item in all_events}
        curation = self._read_curation(project)
        current = [item for item in curation["event_order"] if item in available]
        current.extend(
            str(item["event_id"])
            for item in sorted(all_events, key=lambda value: (value.get("source_order", []), value["event_id"]))
            if str(item["event_id"]) not in current
        )
        active = set(active_ids)
        reordered = iter(event_ids)
        curation["event_order"] = [next(reordered) if item in active else item for item in current]
        self._record(curation, "event.order.update", event_ids, {
            "event_ids": active_ids,
        }, {"event_ids": event_ids})
        return self._commit_curation(project, curation)

    def open_knowledge_card(
        self, project: ProjectV2Descriptor, owner_kind: str, owner_id: str, mode: str
    ) -> dict[str, Any]:
        if owner_kind not in {"character", "world"} or mode not in {"generated", "author"}:
            raise ReconstructionError("知识卡类型或模式无效。")
        owner = self._knowledge_entity(project, owner_id) if owner_kind == "character" else self._knowledge_world(project, owner_id, include_hidden=True)
        if mode == "generated" and owner_kind == "world" and any(
            item["world_id"] == owner_id for item in self.knowledge_snapshot(project).hidden_worlds
        ):
            raise ReconstructionError("隐藏的世界观条目没有活动生成卡。")
        folder = "characters" if owner_kind == "character" else "world"
        path = project.root / "knowledge" / mode / folder / f"{owner_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "author" and not path.is_file():
            atomic_write_text(path, f"# 作者补充：{owner['display_name'] if owner_kind == 'character' else owner['name']}\n\n")
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ReconstructionError("知识卡无法读取。") from exc
        if len(content) > 200_000:
            raise ReconstructionError("知识卡超过 200000 字符上限。")
        return {
            "owner_kind": owner_kind, "owner_id": owner_id, "mode": mode,
            "title": str(owner["display_name"] if owner_kind == "character" else owner["name"]),
            "relative_path": path.relative_to(project.root).as_posix(), "content": content,
            "revision": "card-v1:" + self._digest(content), "read_only": mode == "generated",
        }

    def save_author_card(
        self, project: ProjectV2Descriptor, owner_kind: str, owner_id: str,
        content: str, expected_revision: str,
    ) -> dict[str, Any]:
        if not isinstance(content, str) or len(content) > 200_000:
            raise ReconstructionError("作者卡内容超过 200000 字符上限。")
        current = self.open_knowledge_card(project, owner_kind, owner_id, "author")
        if expected_revision != current["revision"]:
            raise ReconstructionError("作者卡已经变化，请重新打开后再保存。")
        path = project.root / current["relative_path"]
        atomic_write_text(path, content)
        return self.open_knowledge_card(project, owner_kind, owner_id, "author")

    def reopen_proposal(
        self, project: ProjectV2Descriptor, batch_id: str, proposal_id: str
    ) -> dict[str, Any]:
        batch = self.get_batch(project, batch_id)
        if batch.get("status") == "stale":
            raise ReconstructionError("失效批次不能重新审核。")
        proposal = next((item for item in batch["proposals"] if item.get("proposal_id") == proposal_id), None)
        if proposal is None or proposal.get("status") == "pending":
            raise ReconstructionError("候选项不存在或尚未审核。")
        before = proposal["status"]
        proposal["status"] = "pending"
        proposal.pop("reviewed_at", None)
        proposal.pop("review_mode", None)
        batch["status"] = "pending"
        self._write_json(project.root / "proposals" / f"{batch_id}.json", batch)
        index = self._read_collection(project.root / "proposals" / "index.json", "batches")
        index["batches"] = [self._summary(batch) if item.get("batch_id") == batch_id else item for item in index["batches"]]
        self._write_json(project.root / "proposals" / "index.json", index)
        curation = self._read_curation(project)
        self._record(curation, "proposal.reopen", [proposal_id], {"status": before}, {"status": "pending"})
        self._write_json(project.root / "knowledge" / "curation.json", curation)
        self._rebuild_knowledge(project)
        return batch

    def reconcile(self, project: ProjectV2Descriptor) -> None:
        self._rebuild_knowledge(project)

    def _source_state(self, project: ProjectV2Descriptor) -> tuple[str, list[tuple[str, str, str]]]:
        documents = DocumentV2Service()
        chapters = []
        for item in documents.snapshot(project).chapters:
            opened = documents.open_document(project, item.chapter_id)
            chapters.append((item.chapter_id, opened.revision, opened.content))
        revision = self._digest("\0".join(f"{item[0]}:{item[1]}" for item in chapters))
        return revision, chapters

    @staticmethod
    def _checkpoint_path(project: ProjectV2Descriptor) -> Path:
        return project.root / "cache" / "reconstruction-checkpoint.json"

    def _read_checkpoint(self, project: ProjectV2Descriptor, source_revision: str) -> dict[str, Any]:
        path = self._checkpoint_path(project)
        if not path.is_file():
            return {"segments": [], "completed_chapters": []}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return {"segments": [], "completed_chapters": []}
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or value.get("source_revision") != source_revision
            or not isinstance(value.get("segments"), list)
            or not isinstance(value.get("completed_chapters"), list)
        ):
            path.unlink(missing_ok=True)
            return {"segments": [], "completed_chapters": []}
        return value

    @classmethod
    def _segments(cls, chapter_id: str, revision: str, content: str) -> list[dict[str, Any]]:
        results = []
        offset = 0
        for line_number, line in enumerate(content.splitlines(keepends=True), start=1):
            clean = line.strip()
            if clean and not clean.startswith("#"):
                for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", line.rstrip("\r\n")):
                    text = match.group(0).strip()
                    if not text:
                        continue
                    start, end = offset + match.start(), offset + match.end()
                    evidence_id = "evidence_" + cls._digest(f"{chapter_id}\0{revision}\0{start}\0{end}\0{text}")[:24]
                    results.append({
                        "evidence_id": evidence_id, "chapter_id": chapter_id,
                        "chapter_revision": revision, "start": start, "end": end,
                        "anchor": f"{chapter_id}:L{line_number}", "text": text,
                    })
            offset += len(line)
        return results

    @classmethod
    def _extract_proposals(cls, source_revision: str, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names: dict[str, dict[str, Any]] = {}
        relations: dict[tuple[str, str, str], dict[str, Any]] = {}
        worlds: dict[tuple[str, str, str], dict[str, Any]] = {}
        character_fields: dict[tuple[str, str, str], dict[str, Any]] = {}
        events: dict[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], dict[str, Any]] = {}

        def add_name(name: str, segment: dict[str, Any], confidence: float) -> None:
            clean = name.strip()
            if clean in _STOP_NAMES or clean.startswith(("自己", "他们", "她们", "我们", "你们")) or not clean:
                return
            entry = names.setdefault(clean, {"confidence": confidence, "evidence": []})
            entry["confidence"] = max(entry["confidence"], confidence)
            if segment not in entry["evidence"]:
                entry["evidence"].append(segment)

        for segment in segments:
            text = segment["text"]
            for match in _EXPLICIT_ENTITY.finditer(text):
                add_name(match.group("name"), segment, 1.0)
            for match in _ACTION_ENTITY.finditer(text):
                add_name(match.group("name"), segment, 0.78)
            for match in _PAIR.finditer(text):
                add_name(match.group("source"), segment, 0.7)
                add_name(match.group("target"), segment, 0.7)
            relation_matches = [*_EXPLICIT_RELATION.finditer(text), *_NATURAL_RELATION.finditer(text)]
            for match in relation_matches:
                source, target, label = (match.group(key).strip() for key in ("source", "target", "label"))
                add_name(source, segment, 1.0)
                add_name(target, segment, 1.0)
                key = (source, target, label)
                relations.setdefault(key, {"confidence": 1.0, "evidence": []})["evidence"].append(segment)
            for match in _EXPLICIT_WORLD.finditer(text):
                name, category, description = (match.group(key).strip() for key in ("name", "category", "description"))
                key = (name, category, description)
                worlds.setdefault(key, {"confidence": 1.0, "evidence": []})["evidence"].append(segment)
            for match in _EXPLICIT_CHARACTER_FIELD.finditer(text):
                character, field, value = (match.group(key).strip() for key in ("character", "field", "value"))
                if field not in _CHARACTER_FIELDS:
                    continue
                add_name(character, segment, 1.0)
                key = (character, field, value)
                character_fields.setdefault(key, {"confidence": 1.0, "evidence": []})["evidence"].append(segment)
            for match in _EXPLICIT_EVENT.finditer(text):
                time_label, title, description = (match.group(key).strip() for key in ("time", "title", "description"))
                characters = cls._reference_names(match.group("characters"))
                event_worlds = cls._reference_names(match.group("worlds"))
                for character in characters:
                    add_name(character, segment, 1.0)
                key = (time_label, title, description, characters, event_worlds)
                events.setdefault(key, {"confidence": 1.0, "evidence": []})["evidence"].append(segment)

        proposals: list[dict[str, Any]] = []
        for name in sorted(names):
            value = names[name]
            proposals.append({
                "kind": "entity", "identity_key": name.casefold(), "name": name,
                "entity_type": "character", "confidence": value["confidence"],
                "evidence": value["evidence"], "producers": ["local"],
            })
        for (source, target, label), value in sorted(relations.items()):
            proposals.append({
                "kind": "relation", "identity_key": f"{source}\0{target}\0{label}".casefold(),
                "source_name": source, "target_name": target, "label": label,
                "confidence": value["confidence"], "evidence": value["evidence"],
                "producers": ["local"],
            })
        for (name, category, description), value in sorted(worlds.items()):
            proposals.append({
                "kind": "world", "identity_key": f"{name}\0{category}\0{description}".casefold(),
                "name": name, "category": category, "description": description,
                "confidence": value["confidence"], "evidence": value["evidence"],
                "producers": ["local"],
            })
        for (character, field, field_value), value in sorted(character_fields.items()):
            proposals.append({
                "kind": "character_field", "identity_key": f"{character}\0{field}\0{field_value}".casefold(),
                "character_name": character, "field": field, "value": field_value,
                "confidence": value["confidence"], "evidence": value["evidence"],
                "producers": ["local"],
            })
        for (time_label, title, description, characters, event_worlds), value in sorted(events.items()):
            proposals.append({
                "kind": "event", "identity_key": f"{time_label}\0{title}\0{description}\0{'|'.join(characters)}\0{'|'.join(event_worlds)}".casefold(),
                "time_label": time_label, "title": title, "description": description,
                "character_names": list(characters), "world_names": list(event_worlds),
                "confidence": value["confidence"], "evidence": value["evidence"], "producers": ["local"],
            })
        return proposals

    @staticmethod
    def _reference_names(value: str) -> tuple[str, ...]:
        values = [item.strip() for item in re.split(r"[,，、;；]+", value) if item.strip()]
        return tuple(dict.fromkeys(item for item in values if item not in {"-", "—", "无"}))

    @staticmethod
    def _combine_proposals(local: list[dict[str, Any]], remote: list[dict[str, Any]]) -> list[dict[str, Any]]:
        combined: dict[tuple[str, str], dict[str, Any]] = {}
        for item in [*local, *remote]:
            key = (str(item["kind"]), str(item["identity_key"]))
            if key not in combined:
                combined[key] = {**item, "evidence": list(item.get("evidence", []))}
                continue
            existing = combined[key]
            evidence = {value["evidence_id"]: value for value in existing["evidence"]}
            evidence.update({value["evidence_id"]: value for value in item.get("evidence", [])})
            existing["evidence"] = list(evidence.values())
            existing["confidence"] = max(float(existing.get("confidence", 0)), float(item.get("confidence", 0)))
            existing["producers"] = sorted(set(existing.get("producers", [])) | set(item.get("producers", [])))
        return sorted(combined.values(), key=lambda item: (item["kind"], item["identity_key"]))

    @staticmethod
    def _relation_conflict_count(proposals: list[dict[str, Any]]) -> int:
        groups: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
        for item in proposals:
            if item.get("kind") != "relation":
                continue
            source, target = str(item["source_name"]).casefold(), str(item["target_name"]).casefold()
            pair = tuple(sorted((source, target)))
            groups.setdefault(pair, set()).add((source, target, str(item["label"]).casefold()))
        return sum(len(values) - 1 for values in groups.values() if len(values) > 1)

    def _mark_all_stale(self, project: ProjectV2Descriptor, index: dict[str, Any]) -> None:
        changed = False
        for summary in index["batches"]:
            if summary.get("status") == "stale":
                continue
            batch = self.get_batch(project, str(summary["batch_id"]))
            batch["status"] = "stale"
            batch["stale_at"] = self._now()
            self._write_json(project.root / "proposals" / f"{batch['batch_id']}.json", batch)
            summary.update(self._summary(batch))
            changed = True
        if changed:
            self._write_json(project.root / "proposals" / "index.json", index)

    def _accepted_entity_names(self, project: ProjectV2Descriptor, *, excluding_batch: str = "") -> set[str]:
        return {
            str(item["name"])
            for item in self._accepted_proposals(project, excluding_batch=excluding_batch)
            if item.get("kind") == "entity"
        }

    def _accepted_proposals(self, project: ProjectV2Descriptor, *, excluding_batch: str = "") -> list[dict[str, Any]]:
        accepted: list[dict[str, Any]] = []
        index = self._read_collection(project.root / "proposals" / "index.json", "batches")
        for summary in index["batches"]:
            if summary.get("status") == "stale" or summary.get("batch_id") == excluding_batch:
                continue
            batch = self.get_batch(project, str(summary["batch_id"]))
            accepted.extend(item for item in batch["proposals"] if item.get("status") == "accepted")
        return accepted

    def _rebuild_knowledge(self, project: ProjectV2Descriptor) -> None:
        index = self._read_collection(project.root / "proposals" / "index.json", "batches")
        entity_values: dict[str, dict[str, Any]] = {}
        relation_values: dict[tuple[str, str, str], dict[str, Any]] = {}
        world_values: dict[tuple[str, str], dict[str, Any]] = {}
        event_values: dict[str, dict[str, Any]] = {}
        event_proposals: list[dict[str, Any]] = []
        evidence_values: dict[str, dict[str, Any]] = {}
        accepted: list[dict[str, Any]] = []
        for summary in index["batches"]:
            if summary.get("status") == "stale":
                continue
            batch = self.get_batch(project, str(summary["batch_id"]))
            for item in batch["proposals"]:
                if item.get("status") != "accepted":
                    continue
                accepted.append(item)
                for evidence in item.get("evidence", []):
                    evidence_values[evidence["evidence_id"]] = evidence
        for item in accepted:
            if item.get("kind") != "entity":
                continue
            name = item["name"]
            entity_id = self._entity_id(name)
            entry = entity_values.setdefault(entity_id, {
                "entity_id": entity_id, "entity_type": "character", "display_name": name,
                "aliases": [], "profile_fields": {}, "hidden_profile_fields": {}, "profile_evidence_ids": {},
                "evidence_ids": [], "reviewed_at": item.get("reviewed_at", ""),
                "card_relative_path": f"knowledge/generated/characters/{entity_id}.md",
            })
            entry["evidence_ids"] = sorted(set(entry["evidence_ids"]) | {e["evidence_id"] for e in item.get("evidence", [])})
        for item in accepted:
            kind = item.get("kind")
            if kind == "character_field":
                entity_id = self._entity_id(str(item["character_name"]))
                if entity_id not in entity_values:
                    continue
                field = str(item["field"])
                entity_values[entity_id]["profile_fields"][field] = str(item["value"])
                entity_values[entity_id]["profile_evidence_ids"][field] = sorted(
                    {e["evidence_id"] for e in item.get("evidence", [])}
                )
            elif kind == "world":
                name, category = str(item["name"]), str(item["category"])
                key = (name.casefold(), category.casefold())
                world_id = self._world_id(name, category)
                world_values[key] = {
                    "world_id": world_id, "name": name, "category": category,
                    "description": str(item["description"]),
                    "evidence_ids": sorted({e["evidence_id"] for e in item.get("evidence", [])}),
                    "reviewed_at": item.get("reviewed_at", ""),
                    "card_relative_path": f"knowledge/generated/world/{world_id}.md",
                }
            elif kind == "relation":
                source_id, target_id = self._entity_id(item["source_name"]), self._entity_id(item["target_name"])
                if source_id not in entity_values or target_id not in entity_values:
                    continue
                key = (source_id, target_id, item["label"])
                relation_id = "relation_" + self._digest("\0".join(key))[:20]
                entry = relation_values.setdefault(key, {
                    "relation_id": relation_id, "source_entity_id": source_id,
                    "target_entity_id": target_id, "label": item["label"],
                    "evidence_ids": [], "reviewed_at": item.get("reviewed_at", ""),
                })
                entry["evidence_ids"] = sorted(set(entry["evidence_ids"]) | {e["evidence_id"] for e in item.get("evidence", [])})
            elif kind == "event":
                event_proposals.append(item)
        worlds_by_name: dict[str, list[dict[str, Any]]] = {}
        for world in world_values.values():
            worlds_by_name.setdefault(str(world["name"]).casefold(), []).append(world)
        for item in event_proposals:
            event_id = self._event_id(str(item["time_label"]), str(item["title"]))
            participants = [self._entity_id(str(name)) for name in item.get("character_names", [])]
            if any(entity_id not in entity_values for entity_id in participants):
                continue
            referenced_worlds: list[str] = []
            invalid_world = False
            for name in item.get("world_names", []):
                matches = worlds_by_name.get(str(name).casefold(), [])
                if len(matches) != 1:
                    invalid_world = True
                    break
                referenced_worlds.append(str(matches[0]["world_id"]))
            if invalid_world:
                continue
            evidence_items = list(item.get("evidence", []))
            earliest = min(
                ((str(value.get("chapter_id", "")), int(value.get("start", 0))) for value in evidence_items),
                default=("", 0),
            )
            event_values[event_id] = {
                "event_id": event_id, "time_label": str(item["time_label"]), "title": str(item["title"]),
                "description": str(item["description"]), "participant_entity_ids": sorted(set(participants)),
                "world_ids": sorted(set(referenced_worlds)),
                "evidence_ids": sorted({e["evidence_id"] for e in evidence_items}),
                "reviewed_at": item.get("reviewed_at", ""), "source_order": [earliest[0], earliest[1]],
            }
        curation = self._read_curation(project)
        merges = curation["entity_merges"]
        for entity_id, fields in curation["character_field_overrides"].items():
            entity = entity_values.get(entity_id)
            if entity is None or not isinstance(fields, dict):
                continue
            hidden_fields = entity.setdefault("hidden_profile_fields", {})
            for field, override in fields.items():
                if field not in entity.get("profile_fields", {}) or not isinstance(override, dict):
                    continue
                value = str(override.get("value", entity["profile_fields"][field]))
                if override.get("hidden") is True:
                    hidden_fields[field] = value
                    entity["profile_fields"].pop(field, None)
                    entity["profile_evidence_ids"].pop(field, None)
                else:
                    entity["profile_fields"][field] = value
        hidden_world_values: dict[str, dict[str, Any]] = {}
        for world_id, override in curation["world_overrides"].items():
            found = next((item for item in world_values.values() if item.get("world_id") == world_id), None)
            if found is None or not isinstance(override, dict):
                continue
            for key in ("name", "category", "description"):
                if isinstance(override.get(key), str):
                    found[key] = override[key]
            if override.get("hidden") is True:
                hidden_world_values[world_id] = found
                world_values = {key: item for key, item in world_values.items() if item.get("world_id") != world_id}
        hidden_event_values: dict[str, dict[str, Any]] = {}
        for event_id, override in curation["event_overrides"].items():
            found = event_values.get(event_id)
            if found is None or not isinstance(override, dict):
                continue
            for key in ("time_label", "title", "description"):
                if isinstance(override.get(key), str):
                    found[key] = override[key]
            if isinstance(override.get("participant_entity_ids"), list):
                found["participant_entity_ids"] = list(override["participant_entity_ids"])
            if isinstance(override.get("world_ids"), list):
                found["world_ids"] = list(override["world_ids"])
            if override.get("hidden") is True:
                hidden_event_values[event_id] = found
                event_values.pop(event_id, None)
        for entity_id, override in curation["entity_overrides"].items():
            if entity_id not in entity_values or not isinstance(override, dict):
                continue
            if isinstance(override.get("display_name"), str):
                entity_values[entity_id]["display_name"] = override["display_name"]
            if isinstance(override.get("aliases"), list):
                entity_values[entity_id]["aliases"] = list(override["aliases"])
        for source_id in list(entity_values):
            target_id = self._resolve_merge(merges, source_id)
            if target_id == source_id or target_id not in entity_values:
                continue
            source, target = entity_values[source_id], entity_values[target_id]
            target["aliases"] = sorted(set(target.get("aliases", [])) | set(source.get("aliases", [])) | {source["display_name"]})
            target["evidence_ids"] = sorted(set(target["evidence_ids"]) | set(source["evidence_ids"]))
            target_field_state = set(target.get("profile_fields", {})) | set(target.get("hidden_profile_fields", {}))
            for field, value in source.get("profile_fields", {}).items():
                if field not in target_field_state:
                    target["profile_fields"][field] = value
                    target["profile_evidence_ids"][field] = source.get("profile_evidence_ids", {}).get(field, [])
                    target_field_state.add(field)
            for field, value in source.get("hidden_profile_fields", {}).items():
                if field not in target_field_state:
                    target["hidden_profile_fields"][field] = value
                    target_field_state.add(field)
            del entity_values[source_id]

        active_world_ids = {str(item["world_id"]) for item in world_values.values()}
        diagnostics: list[dict[str, Any]] = []
        for event in [*event_values.values(), *hidden_event_values.values()]:
            requested_participants = [str(value) for value in event.get("participant_entity_ids", [])]
            requested_worlds = [str(value) for value in event.get("world_ids", [])]
            event["participant_entity_ids"] = sorted({
                self._resolve_merge(merges, str(entity_id))
                for entity_id in requested_participants
                if self._resolve_merge(merges, str(entity_id)) in entity_values
            })
            event["world_ids"] = sorted({world_id for world_id in requested_worlds if world_id in active_world_ids})
            if event["event_id"] not in event_values:
                continue
            missing_participants = sorted({
                value for value in requested_participants
                if self._resolve_merge(merges, value) not in entity_values
            })
            missing_worlds = sorted({value for value in requested_worlds if value not in active_world_ids})
            if missing_participants:
                diagnostics.append(self._event_diagnostic(
                    "missing_character_link", "warning", event,
                    f"事件“{event['title']}”有 {len(missing_participants)} 个人物连接已失效，请重新选择。",
                ))
            if missing_worlds:
                diagnostics.append(self._event_diagnostic(
                    "inactive_world_link", "warning", event,
                    f"事件“{event['title']}”有 {len(missing_worlds)} 个世界观连接被隐藏或已失效。",
                ))

        source_ordered = sorted(
            [*event_values.values(), *hidden_event_values.values()],
            key=lambda item: (item.get("source_order", []), item["event_id"]),
        )
        available_event_ids = {str(item["event_id"]) for item in source_ordered}
        curated_order = [
            event_id for event_id in curation["event_order"]
            if event_id in available_event_ids
        ]
        curated_order.extend(
            str(item["event_id"]) for item in source_ordered
            if str(item["event_id"]) not in curated_order
        )
        order_index = {event_id: index for index, event_id in enumerate(curated_order)}
        ordered_events = sorted(event_values.values(), key=lambda item: order_index[item["event_id"]])
        for order, event in enumerate(ordered_events, start=1):
            event["order"] = order
        for event in hidden_event_values.values():
            event["order"] = 0

        events_by_time: dict[str, list[dict[str, Any]]] = {}
        for event in ordered_events:
            key = " ".join(str(event["time_label"]).split()).casefold()
            events_by_time.setdefault(key, []).append(event)
        for values in events_by_time.values():
            if len(values) < 2:
                continue
            evidence_ids = sorted({
                evidence_id for event in values for evidence_id in event.get("evidence_ids", [])
            })
            diagnostics.append({
                "diagnostic_id": "diagnostic_" + self._digest(
                    "temporal_overlap\0" + "\0".join(str(item["event_id"]) for item in values)
                )[:20],
                "code": "temporal_overlap", "severity": "notice",
                "message": f"时间标记“{values[0]['time_label']}”对应 {len(values)} 个事件，请确认人工顺序。",
                "event_ids": [str(item["event_id"]) for item in values],
                "evidence_ids": evidence_ids,
            })

        relation_by_id = {item["relation_id"]: item for item in relation_values.values()}
        projected_relations: dict[tuple[str, str, str], dict[str, Any]] = {}
        for relation_id, relation in relation_by_id.items():
            override = curation["relation_overrides"].get(relation_id, {})
            if isinstance(override, dict) and override.get("deleted") is True:
                continue
            source_id = self._resolve_merge(merges, str(override.get("source_entity_id", relation["source_entity_id"])))
            target_id = self._resolve_merge(merges, str(override.get("target_entity_id", relation["target_entity_id"])))
            label = str(override.get("label", relation["label"]))
            if source_id == target_id or source_id not in entity_values or target_id not in entity_values:
                continue
            key = (source_id, target_id, label)
            if key in projected_relations:
                projected_relations[key]["evidence_ids"] = sorted(set(projected_relations[key]["evidence_ids"]) | set(relation["evidence_ids"]))
            else:
                projected_relations[key] = {**relation, "source_entity_id": source_id, "target_entity_id": target_id, "label": label}

        self._write_json(project.root / "knowledge" / "entities.json", {"schema_version": 1, "entities": sorted(entity_values.values(), key=lambda item: item["entity_id"])})
        self._write_json(project.root / "knowledge" / "relations.json", {"schema_version": 1, "relations": sorted(projected_relations.values(), key=lambda item: item["relation_id"])})
        self._write_json(project.root / "knowledge" / "world_rules.json", {
            "schema_version": 1,
            "rules": sorted(world_values.values(), key=lambda item: item["world_id"]),
            "hidden_rules": sorted(hidden_world_values.values(), key=lambda item: item["world_id"]),
        })
        self._write_json(project.root / "knowledge" / "timeline.json", {
            "schema_version": 1,
            "events": ordered_events,
            "hidden_events": sorted(hidden_event_values.values(), key=lambda item: item["event_id"]),
            "diagnostics": sorted(diagnostics, key=lambda item: item["diagnostic_id"]),
        })
        self._write_json(project.root / "provenance" / "evidence.json", {"schema_version": 1, "evidence": sorted(evidence_values.values(), key=lambda item: item["evidence_id"])})
        self._write_json(project.root / "provenance" / "curation_log.json", {"schema_version": 1, "operations": curation["operations"]})
        self._write_generated_cards(project, entity_values.values(), world_values.values())

    def _read_curation(self, project: ProjectV2Descriptor) -> dict[str, Any]:
        path = project.root / "knowledge" / "curation.json"
        default = {
            "schema_version": 1, "entity_overrides": {}, "entity_merges": {},
            "relation_overrides": {}, "character_field_overrides": {},
            "world_overrides": {}, "event_overrides": {}, "event_order": [], "operations": [],
        }
        if not path.is_file():
            return default
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReconstructionError("人工整理状态无法读取。") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ReconstructionError("人工整理状态格式无效。")
        value.setdefault("character_field_overrides", {})
        value.setdefault("world_overrides", {})
        value.setdefault("event_overrides", {})
        value.setdefault("event_order", [])
        for key, expected in (("entity_overrides", dict), ("entity_merges", dict), ("relation_overrides", dict), ("character_field_overrides", dict), ("world_overrides", dict), ("event_overrides", dict), ("event_order", list), ("operations", list)):
            if not isinstance(value.get(key), expected):
                raise ReconstructionError("人工整理状态格式无效。")
        if (
            not all(isinstance(item, str) for item in value["event_order"])
            or len(set(value["event_order"])) != len(value["event_order"])
        ):
            raise ReconstructionError("人工整理状态格式无效。")
        return value

    def _commit_curation(self, project: ProjectV2Descriptor, curation: dict[str, Any]) -> KnowledgeSnapshot:
        self._write_json(project.root / "knowledge" / "curation.json", curation)
        self._rebuild_knowledge(project)
        return self.knowledge_snapshot(project)

    def _knowledge_entity(self, project: ProjectV2Descriptor, entity_id: str) -> dict[str, Any]:
        if re.fullmatch(r"entity_[0-9a-f]{20}", str(entity_id or "")) is None:
            raise ReconstructionError("人物 ID 无效。")
        entities = self._read_collection(project.root / "knowledge" / "entities.json", "entities")["entities"]
        found = next((item for item in entities if item.get("entity_id") == entity_id), None)
        if found is None:
            raise ReconstructionError("人物不存在或已经失效。")
        return found

    def _knowledge_relation(self, project: ProjectV2Descriptor, relation_id: str) -> dict[str, Any]:
        relations = self._read_collection(project.root / "knowledge" / "relations.json", "relations")["relations"]
        found = next((item for item in relations if item.get("relation_id") == relation_id), None)
        if found is None:
            raise ReconstructionError("关系不存在或已经失效。")
        return found

    def _knowledge_world(
        self, project: ProjectV2Descriptor, world_id: str, *, include_hidden: bool = False
    ) -> dict[str, Any]:
        if re.fullmatch(r"world_[0-9a-f]{20}", str(world_id or "")) is None:
            raise ReconstructionError("世界观条目 ID 无效。")
        collection = self._read_collection(project.root / "knowledge" / "world_rules.json", "rules")
        values = list(collection["rules"])
        if include_hidden:
            values.extend(collection.get("hidden_rules", []))
        found = next((item for item in values if item.get("world_id") == world_id), None)
        if found is None:
            raise ReconstructionError("世界观条目不存在、已隐藏或已经失效。")
        return found

    def _knowledge_event(
        self, project: ProjectV2Descriptor, event_id: str, *, include_hidden: bool = False
    ) -> dict[str, Any]:
        if re.fullmatch(r"event_[0-9a-f]{20}", str(event_id or "")) is None:
            raise ReconstructionError("事件 ID 无效。")
        collection = self._read_collection(project.root / "knowledge" / "timeline.json", "events")
        values = list(collection["events"])
        if include_hidden:
            values.extend(collection.get("hidden_events", []))
        found = next((item for item in values if item.get("event_id") == event_id), None)
        if found is None:
            raise ReconstructionError("事件不存在、已隐藏或已经失效。")
        return found

    def _assert_identity_available(self, project: ProjectV2Descriptor, value: str, *, excluding: str) -> None:
        key = value.casefold()
        for entity in self.knowledge_snapshot(project).entities:
            if entity["entity_id"] == excluding:
                continue
            if key == str(entity["display_name"]).casefold() or key in {str(item).casefold() for item in entity.get("aliases", [])}:
                raise ReconstructionError("该名称已经被其他人物使用。")

    @staticmethod
    def _resolve_merge(merges: dict[str, Any], entity_id: str) -> str:
        current, seen = entity_id, set()
        while isinstance(merges.get(current), str) and current not in seen:
            seen.add(current)
            current = merges[current]
        return current

    @classmethod
    def _event_diagnostic(
        cls, code: str, severity: str, event: dict[str, Any], message: str
    ) -> dict[str, Any]:
        return {
            "diagnostic_id": "diagnostic_" + cls._digest(
                f"{code}\0{event['event_id']}"
            )[:20],
            "code": code,
            "severity": severity,
            "message": message,
            "event_ids": [str(event["event_id"])],
            "evidence_ids": list(event.get("evidence_ids", [])),
        }

    @classmethod
    def _record(cls, curation: dict[str, Any], kind: str, subjects: list[str], before: dict[str, Any], after: dict[str, Any]) -> None:
        curation["operations"].append({
            "operation_id": "curation_" + cls._digest(cls._now() + "\0" + kind + "\0" + "\0".join(subjects))[:20],
            "kind": kind, "subject_ids": subjects, "created_at": cls._now(),
            "before": before, "after": after,
        })

    @staticmethod
    def _clean_label(value: str, label: str, maximum: int) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > maximum or any(char in clean for char in "\r\n\0"):
            raise ReconstructionError(f"{label}不能为空且不能超过 {maximum} 个字符。")
        return clean

    @staticmethod
    def _summary(batch: dict[str, Any]) -> dict[str, Any]:
        proposals = batch.get("proposals", [])
        extraction = batch.get("extraction", {})
        return {
            "batch_id": batch["batch_id"], "source_revision": batch["source_revision"],
            "created_at": batch["created_at"], "status": batch["status"],
            "proposal_count": len(proposals),
            "pending_count": sum(1 for item in proposals if item.get("status") == "pending"),
            "accepted_count": sum(1 for item in proposals if item.get("status") == "accepted"),
            "requested_mode": extraction.get("requested_mode", "local"),
            "producer": extraction.get("producer", "local"),
            "fallback_used": extraction.get("fallback_used", False),
        }

    @staticmethod
    def _read_collection(path: Path, key: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReconstructionError(f"重建数据无法读取：{path.name}") from exc
        if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get(key), list):
            raise ReconstructionError(f"重建集合格式无效：{path.name}")
        return value

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        try:
            if path.is_file() and json.loads(path.read_text(encoding="utf-8")) == value:
                return
        except (OSError, UnicodeError, json.JSONDecodeError):
            pass
        atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _entity_id(cls, name: str) -> str:
        return "entity_" + cls._digest(name.strip().casefold())[:20]

    @classmethod
    def _world_id(cls, name: str, category: str) -> str:
        return "world_" + cls._digest(name.strip().casefold() + "\0" + category.strip().casefold())[:20]

    @classmethod
    def _event_id(cls, time_label: str, title: str) -> str:
        return "event_" + cls._digest(time_label.strip().casefold() + "\0" + title.strip().casefold())[:20]

    @classmethod
    def _write_generated_cards(
        cls,
        project: ProjectV2Descriptor,
        entities: Any,
        worlds: Any,
    ) -> None:
        character_dir = project.root / "knowledge" / "generated" / "characters"
        world_dir = project.root / "knowledge" / "generated" / "world"
        character_dir.mkdir(parents=True, exist_ok=True)
        world_dir.mkdir(parents=True, exist_ok=True)
        (project.root / "knowledge" / "author" / "characters").mkdir(parents=True, exist_ok=True)
        (project.root / "knowledge" / "author" / "world").mkdir(parents=True, exist_ok=True)
        expected_characters: set[str] = set()
        for entity in entities:
            filename = f"{entity['entity_id']}.md"
            expected_characters.add(filename)
            fields = entity.get("profile_fields", {})
            rows = "\n".join(
                f"| {cls._markdown_cell(field)} | {cls._markdown_cell(value)} |"
                for field, value in sorted(fields.items())
            ) or "| — | 尚无已审核字段 |"
            rendered = (
                "<!-- NOVALIST GENERATED PROJECTION: DO NOT EDIT -->\n"
                f"# {entity['display_name']}\n\n"
                "本文件由已审核正文证据生成。作者补充内容请保存在独立资料中。\n\n"
                "| 字段 | 已审核内容 |\n|---|---|\n"
                f"{rows}\n"
            )
            atomic_write_text(character_dir / filename, rendered)
        expected_worlds: set[str] = set()
        for world in worlds:
            filename = f"{world['world_id']}.md"
            expected_worlds.add(filename)
            rendered = (
                "<!-- NOVALIST GENERATED PROJECTION: DO NOT EDIT -->\n"
                f"# {world['name']}\n\n"
                f"- 类型：{world['category']}\n"
                f"- 已审核描述：{world['description']}\n\n"
                "本文件由已审核正文证据生成。作者补充内容请保存在独立资料中。\n"
            )
            atomic_write_text(world_dir / filename, rendered)
        for folder, expected in ((character_dir, expected_characters), (world_dir, expected_worlds)):
            for path in folder.glob("*.md"):
                if path.name not in expected:
                    path.unlink(missing_ok=True)

    @staticmethod
    def _markdown_cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
