import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from application.relationship_graph_service import RelationshipGraphService
from core.accepted_memory import memory_hash
from core.project import NovelProject
from core.text_chunking import chapter_content_hash


FIXTURE_PROJECT = (
    Path(__file__).parent
    / "fixtures"
    / "electron_migration"
    / "golden_project"
)


def _digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


class RelationshipGraphServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        shutil.copytree(FIXTURE_PROJECT, self.root)
        self.project = NovelProject(self.root)
        self.service = RelationshipGraphService()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_golden_projection_is_directed_deterministic_and_read_only(self) -> None:
        before = _digests(self.root)

        first = self.service.snapshot(self.project)
        second = self.service.snapshot(self.project)

        self.assertEqual(first, second)
        self.assertEqual(before, _digests(self.root))
        self.assertEqual(first.project_name, "迁移基线：雾港来信")
        self.assertEqual({node.name for node in first.nodes}, {"林砚", "苏乔", "白鸥"})
        by_id = {node.node_id: node.name for node in first.nodes}
        relations = {
            (by_id[edge.source], by_id[edge.target]): edge.label
            for edge in first.edges
        }
        self.assertEqual(
            relations,
            {
                ("林砚", "苏乔"): "互相信任的调查搭档",
                ("林砚", "白鸥"): "身份不明的线人",
                ("苏乔", "林砚"): "需要保护但可以托付后背的同伴",
            },
        )
        self.assertEqual(
            [warning.target or warning.subject for warning in first.warnings],
            ["白鸥"],
        )
        self.assertTrue(first.revision.startswith("graph-v1:"))

    def test_story_state_wins_and_card_alias_resolves_missing_pair(self) -> None:
        state = self.project.load_story_state()
        state["characters"]["林砚"]["relations"] = {"乔姐": "当前共同调查"}
        self.project.save_story_state(state)

        graph = self.service.snapshot(self.project)
        names = {node.node_id: node.name for node in graph.nodes}
        pair_to_edge = {
            (names[edge.source], names[edge.target]): edge for edge in graph.edges
        }

        self.assertEqual(pair_to_edge[("林砚", "苏乔")].label, "当前共同调查")
        self.assertEqual(pair_to_edge[("林砚", "苏乔")].source_kind, "story_state")
        self.assertEqual(pair_to_edge[("苏乔", "林砚")].source_kind, "story_state")

    def test_verified_accepted_memory_adds_evidence_without_creating_edges(self) -> None:
        summary = self.project.load_chapter_summaries()["chapter_01"]
        chapter = self.project.load_chapter("chapter_01")
        payload = {
            "schema_version": 1,
            "chapter_id": "chapter_01",
            "source_hash": chapter_content_hash(chapter.content),
            "summary_hash": memory_hash(summary),
            "digest": {},
            "evidence_facts": [
                {
                    "fact_id": "fact_relation",
                    "category": "relationship",
                    "subject": "林砚",
                    "predicate": "与苏乔成为",
                    "value": "互相信任的调查搭档",
                    "anchor": "chapter_01:p0003",
                    "certainty": "explicit",
                }
            ],
            "context_hash": "test",
            "accepted_at": "2026-09-12T00:00:00+00:00",
            "state": {},
            "state_dependency_hash": "",
            "state_dependency_count": 0,
        }
        payload["record_hash"] = memory_hash(payload)
        accepted = {"schema_version": 1, "chapters": {"chapter_01": payload}}
        (self.project.memory_dir / "accepted_chapter_memory.json").write_text(
            json.dumps(accepted, ensure_ascii=False), encoding="utf-8"
        )

        graph = self.service.snapshot(self.project)
        names = {node.node_id: node.name for node in graph.nodes}
        edge = next(
            edge
            for edge in graph.edges
            if names[edge.source] == "林砚" and names[edge.target] == "苏乔"
        )

        self.assertEqual(len(graph.edges), 3)
        self.assertEqual(edge.evidence[0].chapter_id, "chapter_01")
        self.assertEqual(edge.evidence[0].anchor, "chapter_01:p0003")

    def test_managed_card_relations_fill_pairs_missing_from_story_state(self) -> None:
        state = self.project.load_story_state()
        state["characters"]["林砚"]["relations"] = {}
        state["characters"]["苏乔"]["relations"] = {}
        self.project.save_story_state(state)

        graph = self.service.snapshot(self.project)
        names = {node.node_id: node.name for node in graph.nodes}
        relations = {
            (names[edge.source], names[edge.target]): (edge.label, edge.source_kind)
            for edge in graph.edges
        }

        self.assertEqual(
            relations[("林砚", "苏乔")],
            ("互相信任的调查搭档", "character_card"),
        )
        self.assertEqual(
            relations[("苏乔", "林砚")],
            ("需要保护但可以托付后背的同伴", "character_card"),
        )

    def test_revision_changes_when_graph_source_changes(self) -> None:
        before = self.service.snapshot(self.project).revision
        state = self.project.load_story_state()
        state["characters"]["林砚"]["relations"]["苏乔"] = "新的关系"
        self.project.save_story_state(state)

        after = self.service.snapshot(self.project)

        self.assertNotEqual(before, after.revision)
        self.assertIn("新的关系", after.relation_types)


if __name__ == "__main__":
    unittest.main()
