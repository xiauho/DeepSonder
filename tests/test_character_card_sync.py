import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from core.accepted_memory import ACCEPTED_MEMORY_SCHEMA, memory_hash, save_accepted_memory
from core.character_card_sync import (
    CharacterCardSyncError,
    MANAGED_STATE_END,
    MANAGED_STATE_START,
    apply_character_card_sync,
    build_character_card_sync_proposal,
    parse_managed_state,
)
from core.project import NovelProject, chapter_number_from_id
from core.project_data import ProjectDataStore
from core.text_chunking import chapter_content_hash


class CharacterCardSyncTests(TestCase):
    def _accepted_record(self, project, chapter_id, summary, state, facts):
        coverage = chapter_number_from_id(chapter_id)
        dependencies = []
        if coverage is not None:
            for path in project.list_chapters():
                number = chapter_number_from_id(path.stem)
                if number is not None and number <= coverage:
                    dependencies.append(
                        (
                            number,
                            path.stem,
                            chapter_content_hash(project.load_chapter(path.stem).content),
                        )
                    )
            dependencies.sort()
        payload = {
            "schema_version": ACCEPTED_MEMORY_SCHEMA,
            "chapter_id": chapter_id,
            "source_hash": chapter_content_hash(project.load_chapter(chapter_id).content),
            "summary_hash": memory_hash(summary),
            "digest": {},
            "evidence_facts": facts,
            "context_hash": "context",
            "accepted_at": "2026-01-01T00:00:00+00:00",
            "state": state,
            "state_through_chapter": coverage,
            "state_dependency_hash": memory_hash(dependencies),
            "state_dependency_count": len(dependencies),
        }
        payload["record_hash"] = memory_hash(payload)
        return payload

    def _project_with_memories(self, root: Path):
        project = NovelProject.create(root / "proj", "测试")
        (project.chapters_dir / "chapter_02.md").write_text(
            "# 第二章\n\n## 正文\n林夜突破至四级，抵达北塔，获得星钥，与顾川成为盟友。\n",
            encoding="utf-8",
        )
        card = ProjectDataStore(project).create_canon_entry("character", "林夜")
        summaries = {"chapter_01": "林夜负伤。", "chapter_02": "林夜抵达北塔并突破。"}
        project.save_chapter_summaries(summaries)
        first_state = {
            "current_chapter": 1,
            "characters": {"林夜": {"state": "负伤"}},
        }
        final_state = {
            "current_chapter": 2,
            "characters": {
                "林夜": {
                    "state": "伤势稳定",
                    "location": "北塔",
                    "power_level": "四级",
                    "items": ["星钥"],
                    "relations": {"顾川": "盟友"},
                }
            },
        }
        records = {
            "chapter_01": self._accepted_record(
                project,
                "chapter_01",
                summaries["chapter_01"],
                first_state,
                [
                    {
                        "fact_id": "fact_state",
                        "category": "character_state",
                        "subject": "林夜",
                        "predicate": "状态变为",
                        "value": "负伤",
                        "anchor": "林夜负伤。",
                        "certainty": "explicit",
                    }
                ],
            ),
            "chapter_02": self._accepted_record(
                project,
                "chapter_02",
                summaries["chapter_02"],
                final_state,
                [
                    {
                        "fact_id": "fact_power",
                        "category": "character_state",
                        "subject": "林夜",
                        "predicate": "等级变为",
                        "value": "四级",
                        "anchor": "林夜突破至四级。",
                        "certainty": "explicit",
                    },
                    {
                        "fact_id": "fact_location",
                        "category": "location",
                        "subject": "林夜",
                        "predicate": "抵达",
                        "value": "北塔",
                        "anchor": "林夜抵达北塔。",
                        "certainty": "explicit",
                    },
                    {
                        "fact_id": "fact_item",
                        "category": "item",
                        "subject": "林夜",
                        "predicate": "获得",
                        "value": "星钥",
                        "anchor": "林夜获得星钥。",
                        "certainty": "explicit",
                    },
                    {
                        "fact_id": "fact_relation",
                        "category": "relationship",
                        "subject": "林夜与顾川",
                        "predicate": "关系变为",
                        "value": "盟友",
                        "anchor": "林夜与顾川成为盟友。",
                        "certainty": "inferred",
                    },
                    {
                        "fact_id": "fact_state_2",
                        "category": "character_state",
                        "subject": "林夜",
                        "predicate": "状态变为",
                        "value": "伤势稳定",
                        "anchor": "林夜伤势稳定。",
                        "certainty": "explicit",
                    },
                ],
            ),
        }
        save_accepted_memory(project, records)
        return project, card

    def test_builds_field_level_proposal_from_verified_range(self):
        with TemporaryDirectory() as tmp:
            project, card = self._project_with_memories(Path(tmp))

            proposal = build_character_card_sync_proposal(
                project, card, ("chapter_01", "chapter_02")
            )

            patches = {patch.field: patch for patch in proposal.patches}
            self.assertEqual(
                set(patches), {"state", "location", "power_level", "items", "relations"}
            )
            self.assertEqual(patches["location"].value, "北塔")
            self.assertEqual(patches["items"].value, ["星钥"])
            self.assertTrue(patches["state"].default_selected)
            self.assertFalse(patches["relations"].default_selected)
            self.assertEqual(proposal.as_of_chapter, "chapter_02")

    def test_apply_only_changes_managed_block_and_creates_backup(self):
        with TemporaryDirectory() as tmp:
            project, card = self._project_with_memories(Path(tmp))
            original = card.read_text(encoding="utf-8")
            original = original.replace("## 作者自由备注\n", "## 作者自由备注\n绝不覆盖这段。\n")
            card.write_text(original, encoding="utf-8")
            proposal = build_character_card_sync_proposal(
                project, card, ("chapter_01", "chapter_02")
            )

            result = apply_character_card_sync(
                project, proposal, ("location", "power_level", "items")
            )

            updated = card.read_text(encoding="utf-8")
            managed = parse_managed_state(updated)
            self.assertIn("绝不覆盖这段。", updated)
            self.assertEqual(managed["location"], "北塔")
            self.assertEqual(managed["power_level"], "四级")
            self.assertEqual(managed["items"], ["星钥"])
            self.assertEqual(managed["state"], "")
            self.assertEqual(managed["as_of_chapter"], "chapter_02")
            self.assertEqual(
                (result.backup_path / "character.md").read_text(encoding="utf-8"),
                original,
            )
            manifest = json.loads(
                (result.backup_path / "backup-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["applied_fields"], ["location", "power_level", "items"])

    def test_stale_card_is_not_overwritten(self):
        with TemporaryDirectory() as tmp:
            project, card = self._project_with_memories(Path(tmp))
            proposal = build_character_card_sync_proposal(project, card, ("chapter_02",))
            card.write_text(card.read_text(encoding="utf-8") + "\n作者新改动。\n", encoding="utf-8")

            with self.assertRaisesRegex(CharacterCardSyncError, "已发生变化"):
                apply_character_card_sync(project, proposal, (proposal.patches[0].field,))

            self.assertIn("作者新改动", card.read_text(encoding="utf-8"))

    def test_unverified_chapter_is_rejected(self):
        with TemporaryDirectory() as tmp:
            project, card = self._project_with_memories(Path(tmp))
            records_path = project.memory_dir / "accepted_chapter_memory.json"
            records_path.unlink()

            with self.assertRaisesRegex(CharacterCardSyncError, "尚未采用故事记忆"):
                build_character_card_sync_proposal(project, card, ("chapter_01",))

    def test_changed_dependency_blocks_endpoint_state(self):
        with TemporaryDirectory() as tmp:
            project, card = self._project_with_memories(Path(tmp))
            first = project.chapters_dir / "chapter_01.md"
            first.write_text(first.read_text(encoding="utf-8") + "\n前文被修改。\n", encoding="utf-8")

            with self.assertRaisesRegex(CharacterCardSyncError, "状态依赖已经变化"):
                build_character_card_sync_proposal(project, card, ("chapter_02",))

    def test_legacy_card_gets_an_app_owned_block(self):
        with TemporaryDirectory() as tmp:
            project, card = self._project_with_memories(Path(tmp))
            card.write_text("# 林夜\n\n## 私有设定\n保持原样。\n", encoding="utf-8")
            proposal = build_character_card_sync_proposal(project, card, ("chapter_02",))

            apply_character_card_sync(project, proposal, ("location",))
            updated = card.read_text(encoding="utf-8")

            self.assertIn("## 私有设定\n保持原样。", updated)
            self.assertIn(MANAGED_STATE_START, updated)
            self.assertIn(MANAGED_STATE_END, updated)


if __name__ == "__main__":
    import unittest

    unittest.main()
