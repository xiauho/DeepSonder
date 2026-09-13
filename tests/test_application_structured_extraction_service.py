import json
import tempfile
import threading
import unittest
from pathlib import Path

from application.manuscript_import_service import ManuscriptImportService
from application.project_v2_service import ProjectV2Service
from application.reconstruction_service import ReconstructionService
from application.structured_extraction_service import (
    StructuredExtractionError,
    StructuredExtractionResult,
    StructuredExtractionService,
)
from core.config import DEFAULT_CONFIG


def _segment(evidence_id: str, text: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "chapter_id": "chapter_0001",
        "chapter_revision": "v2:test",
        "start": 0,
        "end": len(text),
        "anchor": "chapter_0001:L1",
        "text": text,
    }


class _Estimator:
    @staticmethod
    def estimate_pair(system: str, user: str) -> int:
        return len(system) + len(user)


class _FakeClient:
    response = ""
    instances = []

    def __init__(self, **kwargs):
        self.options = kwargs
        self.token_estimator = _Estimator()
        self.isolated = False
        self.cleaned = False
        self.calls = []
        self.__class__.instances.append(self)

    def use_isolated_workspace(self):
        self.isolated = True

    def prompt_build_budget(self):
        return 12_000

    def generate(self, system, user, **kwargs):
        self.calls.append((system, user, kwargs))
        return self.response

    def cleanup(self):
        self.cleaned = True


class StructuredExtractionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeClient.instances.clear()

    def test_valid_output_maps_only_known_evidence_and_cleans_workspace(self) -> None:
        _FakeClient.response = json.dumps({
            "entities": [
                {"name": "林砚", "evidence_ids": ["e1"]},
                {"name": "苏乔", "evidence_ids": ["e1"]},
            ],
            "relations": [{
                "source_name": "林砚", "target_name": "苏乔", "label": "搭档",
                "evidence_ids": ["e1"],
            }],
            "worlds": [{
                "name": "雾港", "category": "地点", "description": "终年被浓雾笼罩的港城",
                "evidence_ids": ["e1"],
            }],
            "character_fields": [{
                "character_name": "林砚", "field": "身份", "value": "调查员",
                "evidence_ids": ["e1"],
            }],
            "events": [{
                "time_label": "雨夜", "title": "共同调查", "description": "两人在雾港开始调查",
                "character_names": ["林砚", "苏乔"], "world_names": ["雾港"], "evidence_ids": ["e1"],
            }],
        }, ensure_ascii=False)
        service = StructuredExtractionService(
            config_loader=lambda: dict(DEFAULT_CONFIG), client_factory=_FakeClient,
        )
        result = service.extract(
            "revision", [_segment("e1", "林砚和苏乔共同调查。")],
            cancel_event=threading.Event(), progress_callback=lambda _stage, _progress: None,
        )

        self.assertEqual(len(result.proposals), 6)
        self.assertEqual({item["kind"] for item in result.proposals}, {"entity", "relation", "world", "character_field", "event"})
        self.assertEqual(result.remote_chunk_count, 1)
        client = _FakeClient.instances[0]
        self.assertTrue(client.isolated)
        self.assertTrue(client.cleaned)
        payload = json.loads(client.calls[0][1])
        self.assertEqual(set(payload["evidence"][0]), {"evidence_id", "chapter_id", "text"})

    def test_unknown_evidence_or_extra_fields_rejects_whole_remote_output(self) -> None:
        _FakeClient.response = json.dumps({
            "entities": [{"name": "林砚", "evidence_ids": ["not-sent"], "guess": True}],
            "relations": [],
            "worlds": [],
            "character_fields": [],
            "events": [],
        }, ensure_ascii=False)
        service = StructuredExtractionService(
            config_loader=lambda: dict(DEFAULT_CONFIG), client_factory=_FakeClient,
        )
        with self.assertRaises(StructuredExtractionError):
            service.extract(
                "revision", [_segment("e1", "林砚拆开信封。")],
                cancel_event=threading.Event(), progress_callback=lambda _stage, _progress: None,
            )
        self.assertTrue(_FakeClient.instances[0].cleaned)

    def test_unbounded_character_field_is_rejected(self) -> None:
        _FakeClient.response = json.dumps({
            "entities": [{"name": "林砚", "evidence_ids": ["e1"]}],
            "relations": [],
            "worlds": [],
            "character_fields": [{
                "character_name": "林砚", "field": "模型自由字段", "value": "不允许",
                "evidence_ids": ["e1"],
            }],
            "events": [],
        }, ensure_ascii=False)
        service = StructuredExtractionService(
            config_loader=lambda: dict(DEFAULT_CONFIG), client_factory=_FakeClient,
        )
        with self.assertRaises(StructuredExtractionError):
            service.extract(
                "revision", [_segment("e1", "林砚拆开信封。")],
                cancel_event=threading.Event(), progress_callback=lambda _stage, _progress: None,
            )


class ReconstructionEnhancedProducerTests(unittest.TestCase):
    def _project(self, root: Path):
        source = root / "source.md"
        source.write_text("# 第一章\n\n林砚拆开信封。\n", encoding="utf-8")
        output = root / "output"
        output.mkdir()
        return ProjectV2Service().create_project(
            output, "增强识别", import_plan=ManuscriptImportService().scan(source)
        )

    def test_remote_proposals_are_merged_but_still_pending_review(self) -> None:
        class Extractor:
            def extract(self, _revision, segments, **_kwargs):
                evidence = [segments[0]]
                return StructuredExtractionResult((
                    {"kind": "entity", "identity_key": "林砚", "name": "林砚", "entity_type": "character", "confidence": .76, "evidence": evidence, "producers": ["dsh"]},
                    {"kind": "entity", "identity_key": "苏乔", "name": "苏乔", "entity_type": "character", "confidence": .76, "evidence": evidence, "producers": ["dsh"]},
                    {"kind": "relation", "identity_key": "林砚\0苏乔\0搭档", "source_name": "林砚", "target_name": "苏乔", "label": "搭档", "confidence": .76, "evidence": evidence, "producers": ["dsh"]},
                ), len(segments), 1, 600)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            batch = ReconstructionService(structured_extractor=Extractor()).generate(
                project, extraction_mode="dsh", remote_consent=True,
            )
            self.assertEqual(batch["extraction"]["producer"], "local+dsh")
            self.assertEqual(batch["extraction"]["duplicate_count"], 1)
            self.assertEqual(len(batch["proposals"]), 3)
            self.assertTrue(all(item["status"] == "pending" for item in batch["proposals"]))
            self.assertEqual(ReconstructionService().knowledge_snapshot(project).entities, ())

    def test_remote_failure_falls_back_to_local_without_remote_partial_data(self) -> None:
        class Extractor:
            def extract(self, *_args, **_kwargs):
                raise StructuredExtractionError("invalid")

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            batch = ReconstructionService(structured_extractor=Extractor()).generate(
                project, extraction_mode="dsh", remote_consent=True,
            )
            self.assertTrue(batch["extraction"]["fallback_used"])
            self.assertEqual(batch["extraction"]["producer"], "local")
            self.assertNotIn("苏乔", {item.get("name") for item in batch["proposals"]})

    def test_remote_mode_requires_explicit_consent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            with self.assertRaises(ValueError):
                ReconstructionService().generate(project, extraction_mode="dsh")


if __name__ == "__main__":
    unittest.main()
