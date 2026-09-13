import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from core.character_card_sync import parse_managed_state
from core.foreshadowing import ForeshadowingStore
from core.project import NovelProject
from core.project_data import ProjectDataStore
from core.project_migrations import migrate_project, plan_project_migration
from core.project_schema import PROJECT_SCHEMA_VERSION


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "electron_migration"
FIXTURE_MANIFEST = FIXTURE_ROOT / "fixture-manifest.json"
GOLDEN_PROJECT = FIXTURE_ROOT / "golden_project"


def _file_digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class ElectronMigrationFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
        if cls.manifest.get("fixture_schema_version") != 1:
            raise ValueError("Unsupported Electron migration fixture schema")
        cls.expected = cls.manifest["expected"]

    def test_fixture_is_a_current_project_and_requires_no_migration(self) -> None:
        with TemporaryDirectory() as tmp:
            copied = Path(tmp) / "golden_project"
            shutil.copytree(GOLDEN_PROJECT, copied)
            before = _file_digests(copied)

            plan = plan_project_migration(copied)
            result = migrate_project(copied)

            self.assertFalse(plan.required)
            self.assertFalse(result.migrated)
            self.assertEqual(plan.from_schema, PROJECT_SCHEMA_VERSION)
            self.assertEqual(
                plan.from_schema,
                self.expected["project_schema_version"],
            )
            self.assertEqual(result.to_schema, PROJECT_SCHEMA_VERSION)
            self.assertEqual(before, _file_digests(copied))

    def test_fixture_matches_recorded_project_surface(self) -> None:
        project = NovelProject(GOLDEN_PROJECT)
        self.assertEqual(project.name, self.expected["project_name"])
        self.assertEqual(
            [path.stem for path in project.list_chapters()],
            self.expected["chapter_ids"],
        )
        self.assertEqual(
            [path.stem for path in project.list_characters()],
            self.expected["character_names"],
        )

        state = project.load_story_state()
        self.assertEqual(state["current_chapter"], self.expected["current_chapter"])
        relationships = sorted(
            (source, target, relation)
            for source, details in state["characters"].items()
            for target, relation in details.get("relations", {}).items()
        )
        self.assertEqual(
            relationships,
            sorted(tuple(item) for item in self.expected["directed_relationships"]),
        )

        notes = ForeshadowingStore(project).load_notes()
        self.assertEqual(
            sum(note["status"] == "open" for note in notes),
            self.expected["open_foreshadowing"],
        )
        self.assertEqual(
            sum(note["status"] == "resolved" for note in notes),
            self.expected["resolved_foreshadowing"],
        )
        registry = ProjectDataStore(project).load_system_registry()
        self.assertEqual(
            registry["entries"]["canon/power/回声现象.md"]["importance"],
            "core",
        )

    def test_fixture_exercises_extra_sections_and_managed_character_state(self) -> None:
        project = NovelProject(GOLDEN_PROJECT)
        chapter = project.load_chapter("chapter_02")
        self.assertIn(("作者备注", "蓝色纤维与港务厅制服有关，但人物尚未确认。"), chapter.extra_sections)

        card_path = project.canon_dir / "characters" / "林砚.md"
        managed = parse_managed_state(card_path.read_text(encoding="utf-8"))
        self.assertEqual(managed["as_of_chapter"], "chapter_02")
        self.assertEqual(managed["items"], ["银钥匙", "黑色笔记本"])
        self.assertEqual(
            managed["relations"],
            {
                "苏乔": "互相信任的调查搭档",
                "白鸥": "身份不明的线人",
            },
        )


if __name__ == "__main__":
    unittest.main()
