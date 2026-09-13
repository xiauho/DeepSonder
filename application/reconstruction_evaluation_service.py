"""Offline quality evaluation for evidence-backed knowledge reconstruction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .reconstruction_service import ReconstructionService


_SUBJECTS = ("entities", "relations", "worlds", "character_fields", "events")
_CHARACTER_FIELDS = {"身份", "外貌", "性格", "目标", "能力", "阵营", "状态"}


class ReconstructionEvaluationError(ValueError):
    """Raised when an evaluation corpus is unsafe or malformed."""


@dataclass(frozen=True)
class EvaluationReport:
    corpus_id: str
    case_count: int
    modes: dict[str, Any]
    gates: tuple[dict[str, Any], ...]
    schema_version: int = 1

    @property
    def passed(self) -> bool:
        return all(item["passed"] for item in self.gates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "corpus_id": self.corpus_id,
            "case_count": self.case_count,
            "passed": self.passed,
            "modes": self.modes,
            "gates": list(self.gates),
        }


class ReconstructionEvaluationService:
    """Measure exact proposal extraction without reading user projects."""

    def load_corpus(self, path: Path) -> dict[str, Any]:
        try:
            corpus = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReconstructionEvaluationError("识别评估语料无法读取。") from exc
        if not isinstance(corpus, dict) or set(corpus) != {
            "schema_version", "corpus_id", "privacy", "quality_gates", "cases",
        }:
            raise ReconstructionEvaluationError("识别评估语料根结构无效。")
        privacy = corpus.get("privacy")
        if (corpus.get("schema_version") not in {1, 2, 3} or not isinstance(corpus.get("corpus_id"), str)
                or not isinstance(privacy, dict) or privacy.get("synthetic") is not True
                or privacy.get("contains_personal_data") is not False):
            raise ReconstructionEvaluationError("评估语料必须明确标记为无个人数据的合成内容。")
        cases = corpus.get("cases")
        if not isinstance(cases, list) or not 1 <= len(cases) <= 1_000:
            raise ReconstructionEvaluationError("评估用例数量无效。")
        if not isinstance(corpus.get("quality_gates"), dict):
            raise ReconstructionEvaluationError("质量门槛无效。")
        seen: set[str] = set()
        source_chars = 0
        for case in cases:
            self._validate_case(case, seen, schema_version=int(corpus["schema_version"]))
            source_chars += sum(len(item["content"]) for item in case["chapters"])
        if source_chars > 1_000_000:
            raise ReconstructionEvaluationError("评估语料总文本超过安全上限。")
        return corpus

    def evaluate(self, corpus: dict[str, Any]) -> EvaluationReport:
        service = ReconstructionService()
        observations: dict[str, dict[str, list[tuple[set[Any], set[Any], list[tuple[float, bool]]]]]] = {
            mode: {subject: [] for subject in _SUBJECTS} for mode in ("local", "combined")
        }
        cases_report: dict[str, list[dict[str, Any]]] = {"local": [], "combined": []}
        duplicate_total = 0
        conflict_total = 0
        local_conflict_total = 0
        for case in corpus["cases"]:
            segments: list[dict[str, Any]] = []
            for chapter in case["chapters"]:
                segments.extend(service._segments(chapter["chapter_id"], "evaluation:v1", chapter["content"]))
            local = service._extract_proposals("evaluation:v1", segments)
            enhanced = self._recorded_proposals(case.get("recorded_enhanced", {}))
            combined = service._combine_proposals(local, enhanced)
            local_keys = {(item["kind"], item["identity_key"]) for item in local}
            enhanced_keys = {(item["kind"], item["identity_key"]) for item in enhanced}
            duplicate_total += len(local_keys & enhanced_keys)
            conflict_total += service._relation_conflict_count(combined)
            local_conflict_total += service._relation_conflict_count(local)
            expected_entities = {str(name).strip().casefold() for name in case["expected"]["entities"]}
            expected_relations = {
                (item["source"].strip().casefold(), item["target"].strip().casefold(), item["label"].strip().casefold())
                for item in case["expected"]["relations"]
            }
            expected_worlds = {
                (item["name"].strip().casefold(), item["category"].strip().casefold(), item["description"].strip().casefold())
                for item in case["expected"].get("worlds", [])
            }
            expected_character_fields = {
                (item["character"].strip().casefold(), item["field"].strip().casefold(), item["value"].strip().casefold())
                for item in case["expected"].get("character_fields", [])
            }
            expected_events = {
                (item["time_label"].strip().casefold(), item["title"].strip().casefold(), item["description"].strip().casefold(),
                 tuple(name.strip().casefold() for name in item["characters"]), tuple(name.strip().casefold() for name in item["worlds"]))
                for item in case["expected"].get("events", [])
            }
            for mode, proposals in (("local", local), ("combined", combined)):
                predicted_entities = {item["name"].casefold() for item in proposals if item["kind"] == "entity"}
                predicted_relations = {
                    (item["source_name"].casefold(), item["target_name"].casefold(), item["label"].casefold())
                    for item in proposals if item["kind"] == "relation"
                }
                predicted_worlds = {
                    (item["name"].casefold(), item["category"].casefold(), item["description"].casefold())
                    for item in proposals if item["kind"] == "world"
                }
                predicted_character_fields = {
                    (item["character_name"].casefold(), item["field"].casefold(), item["value"].casefold())
                    for item in proposals if item["kind"] == "character_field"
                }
                predicted_events = {
                    (item["time_label"].casefold(), item["title"].casefold(), item["description"].casefold(),
                     tuple(name.casefold() for name in item["character_names"]), tuple(name.casefold() for name in item["world_names"]))
                    for item in proposals if item["kind"] == "event"
                }
                entity_confidence = [
                    (float(item["confidence"]), item["name"].casefold() in expected_entities)
                    for item in proposals if item["kind"] == "entity"
                ]
                relation_confidence = [
                    (float(item["confidence"]), (
                        item["source_name"].casefold(), item["target_name"].casefold(), item["label"].casefold(),
                    ) in expected_relations)
                    for item in proposals if item["kind"] == "relation"
                ]
                world_confidence = [
                    (float(item["confidence"]), (item["name"].casefold(), item["category"].casefold(), item["description"].casefold()) in expected_worlds)
                    for item in proposals if item["kind"] == "world"
                ]
                field_confidence = [
                    (float(item["confidence"]), (item["character_name"].casefold(), item["field"].casefold(), item["value"].casefold()) in expected_character_fields)
                    for item in proposals if item["kind"] == "character_field"
                ]
                event_confidence = [
                    (float(item["confidence"]), (
                        item["time_label"].casefold(), item["title"].casefold(), item["description"].casefold(),
                        tuple(name.casefold() for name in item["character_names"]), tuple(name.casefold() for name in item["world_names"]),
                    ) in expected_events)
                    for item in proposals if item["kind"] == "event"
                ]
                values = {
                    "entities": (predicted_entities, expected_entities, entity_confidence),
                    "relations": (predicted_relations, expected_relations, relation_confidence),
                    "worlds": (predicted_worlds, expected_worlds, world_confidence),
                    "character_fields": (predicted_character_fields, expected_character_fields, field_confidence),
                    "events": (predicted_events, expected_events, event_confidence),
                }
                for subject, observation in values.items():
                    observations[mode][subject].append(observation)
                cases_report[mode].append({
                    "case_id": case["case_id"],
                    "entity": self._counts(predicted_entities, expected_entities),
                    "relation": self._counts(predicted_relations, expected_relations),
                    "world": self._counts(predicted_worlds, expected_worlds),
                    "character_field": self._counts(predicted_character_fields, expected_character_fields),
                    "event": self._counts(predicted_events, expected_events),
                })
        modes: dict[str, Any] = {}
        for mode in ("local", "combined"):
            modes[mode] = {
                **{subject: self._aggregate(observations[mode][subject]) for subject in _SUBJECTS},
                "cases": cases_report[mode],
                "diagnostics": {
                    "duplicates_merged": duplicate_total if mode == "combined" else 0,
                    "relation_conflicts": conflict_total if mode == "combined" else local_conflict_total,
                },
            }
        gates = tuple(self._evaluate_gates(corpus["quality_gates"], modes))
        return EvaluationReport(
            corpus["corpus_id"], len(corpus["cases"]), modes, gates,
            schema_version=int(corpus["schema_version"]),
        )

    @staticmethod
    def _validate_case(case: Any, seen: set[str], *, schema_version: int) -> None:
        required = {"case_id", "chapters", "expected"}
        allowed = required | {"recorded_enhanced"}
        if not isinstance(case, dict) or not required.issubset(case) or set(case) - allowed:
            raise ReconstructionEvaluationError("评估用例字段无效。")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ReconstructionEvaluationError("评估用例 ID 无效或重复。")
        seen.add(case_id)
        chapters = case.get("chapters")
        if not isinstance(chapters, list) or not chapters or any(
            not isinstance(item, dict) or set(item) != {"chapter_id", "content"}
            or not isinstance(item["chapter_id"], str) or not isinstance(item["content"], str)
            for item in chapters
        ):
            raise ReconstructionEvaluationError("评估章节结构无效。")
        expected = case.get("expected")
        expected_keys = {"entities", "relations"} if schema_version == 1 else set(_SUBJECTS[:4] if schema_version == 2 else _SUBJECTS)
        if not isinstance(expected, dict) or set(expected) != expected_keys or not all(isinstance(expected[key], list) for key in expected_keys):
            raise ReconstructionEvaluationError("评估标注结构无效。")
        if not all(isinstance(item, str) and item.strip() for item in expected["entities"]):
            raise ReconstructionEvaluationError("评估人物标注无效。")
        for relation in expected["relations"]:
            if not isinstance(relation, dict) or set(relation) != {"source", "target", "label"} or not all(isinstance(relation[key], str) and relation[key].strip() for key in relation):
                raise ReconstructionEvaluationError("评估关系标注无效。")
        for world in expected.get("worlds", []):
            if not isinstance(world, dict) or set(world) != {"name", "category", "description"} or not all(isinstance(world[key], str) and world[key].strip() for key in world):
                raise ReconstructionEvaluationError("评估世界观标注无效。")
        for field in expected.get("character_fields", []):
            if not isinstance(field, dict) or set(field) != {"character", "field", "value"} or not all(isinstance(field[key], str) and field[key].strip() for key in field):
                raise ReconstructionEvaluationError("评估角色字段标注无效。")
            if field["field"].strip() not in _CHARACTER_FIELDS:
                raise ReconstructionEvaluationError("评估角色字段不在允许范围内。")
        for event in expected.get("events", []):
            if not isinstance(event, dict) or set(event) != {"time_label", "title", "description", "characters", "worlds"}:
                raise ReconstructionEvaluationError("评估事件标注无效。")
            if not all(isinstance(event[key], str) and event[key].strip() for key in ("time_label", "title", "description")):
                raise ReconstructionEvaluationError("评估事件文本无效。")
            if not all(isinstance(event[key], list) and all(isinstance(item, str) and item.strip() for item in event[key]) for key in ("characters", "worlds")):
                raise ReconstructionEvaluationError("评估事件引用无效。")
        empty_enhanced = {key: [] for key in expected_keys}
        enhanced = case.get("recorded_enhanced", empty_enhanced)
        if not isinstance(enhanced, dict) or set(enhanced) != expected_keys or not all(isinstance(enhanced[key], list) for key in expected_keys):
            raise ReconstructionEvaluationError("记录的增强候选无效。")

    @staticmethod
    def _recorded_proposals(value: dict[str, Any]) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        for item in value.get("entities", []):
            if not isinstance(item, dict) or set(item) != {"name", "confidence"}:
                raise ReconstructionEvaluationError("记录的增强人物字段无效。")
            name = str(item["name"]).strip()
            try:
                confidence = float(item["confidence"])
            except (TypeError, ValueError) as exc:
                raise ReconstructionEvaluationError("记录的增强人物置信度无效。") from exc
            if not name or not 0 <= confidence <= 1:
                raise ReconstructionEvaluationError("记录的增强人物值无效。")
            proposals.append({"kind": "entity", "identity_key": name.casefold(), "name": name, "confidence": confidence, "evidence": [], "producers": ["dsh"]})
        for item in value.get("relations", []):
            if not isinstance(item, dict) or set(item) != {"source", "target", "label", "confidence"}:
                raise ReconstructionEvaluationError("记录的增强关系字段无效。")
            source, target, label = (str(item[key]).strip() for key in ("source", "target", "label"))
            try:
                confidence = float(item["confidence"])
            except (TypeError, ValueError) as exc:
                raise ReconstructionEvaluationError("记录的增强关系置信度无效。") from exc
            if not source or not target or not label or source == target or not 0 <= confidence <= 1:
                raise ReconstructionEvaluationError("记录的增强关系值无效。")
            proposals.append({
                "kind": "relation", "identity_key": f"{source}\0{target}\0{label}".casefold(),
                "source_name": source, "target_name": target, "label": label,
                "confidence": confidence, "evidence": [], "producers": ["dsh"],
            })
        for item in value.get("worlds", []):
            if not isinstance(item, dict) or set(item) != {"name", "category", "description", "confidence"}:
                raise ReconstructionEvaluationError("记录的增强世界观字段无效。")
            name, category, description = (str(item[key]).strip() for key in ("name", "category", "description"))
            try:
                confidence = float(item["confidence"])
            except (TypeError, ValueError) as exc:
                raise ReconstructionEvaluationError("记录的增强世界观置信度无效。") from exc
            if not name or not category or not description or not 0 <= confidence <= 1:
                raise ReconstructionEvaluationError("记录的增强世界观值无效。")
            proposals.append({
                "kind": "world", "identity_key": f"{name}\0{category}\0{description}".casefold(),
                "name": name, "category": category, "description": description,
                "confidence": confidence, "evidence": [], "producers": ["dsh"],
            })
        for item in value.get("character_fields", []):
            if not isinstance(item, dict) or set(item) != {"character", "field", "value", "confidence"}:
                raise ReconstructionEvaluationError("记录的增强角色字段无效。")
            character, field, field_value = (str(item[key]).strip() for key in ("character", "field", "value"))
            try:
                confidence = float(item["confidence"])
            except (TypeError, ValueError) as exc:
                raise ReconstructionEvaluationError("记录的增强角色字段置信度无效。") from exc
            if not character or not field or not field_value or not 0 <= confidence <= 1:
                raise ReconstructionEvaluationError("记录的增强角色字段值无效。")
            if field not in _CHARACTER_FIELDS:
                raise ReconstructionEvaluationError("记录的增强角色字段不在允许范围内。")
            proposals.append({
                "kind": "character_field", "identity_key": f"{character}\0{field}\0{field_value}".casefold(),
                "character_name": character, "field": field, "value": field_value,
                "confidence": confidence, "evidence": [], "producers": ["dsh"],
            })
        for item in value.get("events", []):
            if not isinstance(item, dict) or set(item) != {"time_label", "title", "description", "characters", "worlds", "confidence"}:
                raise ReconstructionEvaluationError("记录的增强事件字段无效。")
            time_label, title, description = (str(item[key]).strip() for key in ("time_label", "title", "description"))
            characters = [str(name).strip() for name in item["characters"]] if isinstance(item["characters"], list) else []
            worlds = [str(name).strip() for name in item["worlds"]] if isinstance(item["worlds"], list) else []
            try:
                confidence = float(item["confidence"])
            except (TypeError, ValueError) as exc:
                raise ReconstructionEvaluationError("记录的增强事件置信度无效。") from exc
            if not time_label or not title or not description or any(not name for name in [*characters, *worlds]) or not 0 <= confidence <= 1:
                raise ReconstructionEvaluationError("记录的增强事件值无效。")
            proposals.append({
                "kind": "event", "identity_key": f"{time_label}\0{title}\0{description}\0{'|'.join(characters)}\0{'|'.join(worlds)}".casefold(),
                "time_label": time_label, "title": title, "description": description,
                "character_names": characters, "world_names": worlds,
                "confidence": confidence, "evidence": [], "producers": ["dsh"],
            })
        return proposals

    @staticmethod
    def _counts(predicted: set[Any], expected: set[Any]) -> dict[str, int]:
        return {"tp": len(predicted & expected), "fp": len(predicted - expected), "fn": len(expected - predicted)}

    @classmethod
    def _aggregate(cls, observations: list[tuple[set[Any], set[Any], list[tuple[float, bool]]]]) -> dict[str, Any]:
        counts = {"tp": 0, "fp": 0, "fn": 0}
        confidence: list[tuple[float, bool]] = []
        for predicted, expected, values in observations:
            for key, value in cls._counts(predicted, expected).items():
                counts[key] += value
            confidence.extend(values)
        precision = cls._ratio(counts["tp"], counts["tp"] + counts["fp"])
        recall = cls._ratio(counts["tp"], counts["tp"] + counts["fn"])
        f1 = cls._ratio(2 * precision * recall, precision + recall)
        return {**counts, "precision": precision, "recall": recall, "f1": f1, "calibration": cls._calibration(confidence)}

    @staticmethod
    def _calibration(values: list[tuple[float, bool]]) -> dict[str, Any]:
        buckets = []
        error = 0.0
        for lower, upper in ((0.0, 0.6), (0.6, 0.8), (0.8, 1.000001)):
            selected = [(confidence, correct) for confidence, correct in values if lower <= confidence < upper]
            if not selected:
                continue
            average = sum(item[0] for item in selected) / len(selected)
            accuracy = sum(item[1] for item in selected) / len(selected)
            error += len(selected) / max(1, len(values)) * abs(average - accuracy)
            buckets.append({"lower": lower, "upper": min(1.0, upper), "count": len(selected), "average_confidence": round(average, 4), "accuracy": round(accuracy, 4)})
        return {"ece": round(error, 4), "buckets": buckets}

    @staticmethod
    def _ratio(numerator: float, denominator: float) -> float:
        return round(numerator / denominator, 4) if denominator else 1.0

    @staticmethod
    def _evaluate_gates(gates: dict[str, Any], modes: dict[str, Any]) -> list[dict[str, Any]]:
        results = []
        for mode, subjects in gates.items():
            if mode not in modes or not isinstance(subjects, dict):
                raise ReconstructionEvaluationError("质量门槛模式无效。")
            for subject, metrics in subjects.items():
                if subject not in set(_SUBJECTS) or not isinstance(metrics, dict):
                    raise ReconstructionEvaluationError("质量门槛对象无效。")
                for metric, minimum in metrics.items():
                    if metric not in {"precision", "recall", "f1"} or not isinstance(minimum, (int, float)) or not 0 <= minimum <= 1:
                        raise ReconstructionEvaluationError("质量门槛指标无效。")
                    actual = modes[mode][subject][metric]
                    results.append({"mode": mode, "subject": subject, "metric": metric, "minimum": float(minimum), "actual": actual, "passed": actual >= minimum})
        return results
