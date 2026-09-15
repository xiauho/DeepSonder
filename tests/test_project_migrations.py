import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.project import NovelProject
from core.project_migrations import (
    ProjectMigrationError,
    inspect_project_schema,
    migrate_project,
    plan_project_migration,
)
from core.project_schema import (
    PROJECT_MANIFEST_RELATIVE_PATH,
    PROJECT_MIGRATION_JOURNAL_RELATIVE_PATH,
    PROJECT_SCHEMA_VERSION,
)


class ProjectMigrationTests(TestCase):
    def test_new_project_is_current_and_needs_no_migration(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")

            plan = plan_project_migration(project.root)
            result = migrate_project(project.root)

            self.assertEqual(inspect_project_schema(project.root), PROJECT_SCHEMA_VERSION)
            self.assertFalse(plan.required)
            self.assertFalse(result.migrated)
            self.assertIsNone(result.backup_path)

    def test_legacy_project_migrates_once_with_backup_and_preserves_prose(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            chapter_path = project.chapters_dir / "chapter_01.md"
            prose_hash = hashlib.sha256(chapter_path.read_bytes()).hexdigest()
            state = project.load_story_state()
            state["foreshadowing"] = ["古剑来历"]
            project.save_story_state(state)
            project.style_guide_path.unlink()
            project.core_power_path.unlink()
            (project.memory_dir / "foreshadowing.json").unlink()
            project.system_registry_path.write_text(
                '{"version":1,"entries":{"power/能力体系设定.md":'
                '{"type":"ability","importance":"core"}}}',
                encoding="utf-8",
            )
            (project.root / PROJECT_MANIFEST_RELATIVE_PATH).unlink()

            plan = plan_project_migration(project.root)
            self.assertFalse((project.root / PROJECT_MANIFEST_RELATIVE_PATH).exists())
            self.assertFalse((project.memory_dir / "foreshadowing.json").exists())
            result = migrate_project(project.root)
            second = migrate_project(project.root)

            self.assertTrue(plan.required)
            self.assertTrue(result.migrated)
            self.assertTrue(result.backup_path.is_dir())
            self.assertFalse(second.migrated)
            self.assertEqual(hashlib.sha256(chapter_path.read_bytes()).hexdigest(), prose_hash)
            self.assertNotIn("foreshadowing", project.load_story_state())
            notes = json.loads(
                (project.memory_dir / "foreshadowing.json").read_text(encoding="utf-8")
            )["items"]
            self.assertEqual([item["title"] for item in notes], ["古剑来历"])
            registry = project.system_registry_path.read_text(encoding="utf-8")
            self.assertIn("canon/power/能力体系设定.md", registry)
            self.assertTrue(project.style_guide_path.is_file())
            self.assertTrue(project.core_power_path.is_file())

    def test_write_failure_restores_all_existing_and_new_files(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            state = project.load_story_state()
            state["foreshadowing"] = ["不得丢失"]
            project.save_story_state(state)
            original_state = (project.memory_dir / "story_state.json").read_text(
                encoding="utf-8"
            )
            foreshadowing_path = project.memory_dir / "foreshadowing.json"
            foreshadowing_path.unlink()
            manifest_path = project.root / PROJECT_MANIFEST_RELATIVE_PATH
            manifest_path.unlink()

            from core import project_migrations

            original_write = project_migrations.atomic_write_text

            def fail_manifest(path, text, *, encoding="utf-8"):
                if Path(path) == manifest_path:
                    raise OSError("simulated manifest failure")
                return original_write(path, text, encoding=encoding)

            with patch(
                "core.project_migrations.atomic_write_text",
                side_effect=fail_manifest,
            ):
                with self.assertRaises(ProjectMigrationError):
                    migrate_project(project.root)

            self.assertEqual(
                (project.memory_dir / "story_state.json").read_text(encoding="utf-8"),
                original_state,
            )
            self.assertFalse(foreshadowing_path.exists())
            self.assertFalse(manifest_path.exists())
            self.assertFalse(
                (project.root / PROJECT_MIGRATION_JOURNAL_RELATIVE_PATH).exists()
            )

    def test_future_schema_is_rejected_without_writes(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            manifest_path = project.root / PROJECT_MANIFEST_RELATIVE_PATH
            manifest_path.write_text(
                json.dumps({"schema_version": PROJECT_SCHEMA_VERSION + 1}),
                encoding="utf-8",
            )
            before = manifest_path.read_bytes()

            with self.assertRaisesRegex(ProjectMigrationError, "高于当前程序支持"):
                migrate_project(project.root)

            self.assertEqual(manifest_path.read_bytes(), before)

    def test_current_schema_missing_required_file_is_not_silently_repaired(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            project.style_guide_path.unlink()

            with self.assertRaisesRegex(ProjectMigrationError, "缺少必需文件"):
                migrate_project(project.root)

            self.assertFalse(project.style_guide_path.exists())

    def test_interrupted_migration_is_recovered_before_retry(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            state_path = project.memory_dir / "story_state.json"
            original_state = project.load_story_state()
            original_state["foreshadowing"] = ["恢复后再迁移"]
            project.save_story_state(original_state)
            original_text = state_path.read_text(encoding="utf-8")
            foreshadowing_path = project.memory_dir / "foreshadowing.json"
            foreshadowing_path.unlink()
            (project.root / PROJECT_MANIFEST_RELATIVE_PATH).unlink()

            backup = project.root / ".novalist" / "backups" / "migration-test"
            backup_state = backup / "files" / "memory" / "story_state.json"
            backup_state.parent.mkdir(parents=True)
            backup_state.write_text(original_text, encoding="utf-8")
            backed_up = ["memory/story_state.json"]
            created = ["memory/foreshadowing.json", ".novalist/project.json"]
            (backup / "backup-manifest.json").write_text(
                json.dumps(
                    {"schema_version": 1, "backed_up": backed_up, "created": created}
                ),
                encoding="utf-8",
            )
            state_path.write_text('{"current_chapter":1,"characters":{}}', encoding="utf-8")
            foreshadowing_path.write_text('{"version":1,"items":[]}', encoding="utf-8")
            journal_path = project.root / PROJECT_MIGRATION_JOURNAL_RELATIVE_PATH
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "from_schema": 0,
                        "to_schema": PROJECT_SCHEMA_VERSION,
                        "backup_path": backup.relative_to(project.root).as_posix(),
                        "backed_up": backed_up,
                        "created": created,
                    }
                ),
                encoding="utf-8",
            )

            result = migrate_project(project.root)

            self.assertTrue(result.recovered_interrupted_migration)
            self.assertTrue(result.migrated)
            notes = json.loads(foreshadowing_path.read_text(encoding="utf-8"))["items"]
            self.assertEqual([item["title"] for item in notes], ["恢复后再迁移"])
            self.assertFalse(journal_path.exists())

    def test_recovery_journal_cannot_target_author_chapters(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            chapter = project.chapters_dir / "chapter_01.md"
            backup = project.root / ".novalist" / "backups" / "malicious"
            backup.mkdir(parents=True)
            created = ["outline/chapters/chapter_01.md"]
            (backup / "backup-manifest.json").write_text(
                json.dumps({"schema_version": 1, "backed_up": [], "created": created}),
                encoding="utf-8",
            )
            journal_path = project.root / PROJECT_MIGRATION_JOURNAL_RELATIVE_PATH
            journal_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "from_schema": 0,
                        "to_schema": PROJECT_SCHEMA_VERSION,
                        "backup_path": backup.relative_to(project.root).as_posix(),
                        "backed_up": [],
                        "created": created,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ProjectMigrationError, "不允许修改"):
                migrate_project(project.root)

            self.assertTrue(chapter.is_file())
