import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from application.manuscript_import_service import ManuscriptImportService
from application.project_service import ProjectService
from application.project_v2_service import ProjectV2Service
from core.project_migrations import ProjectMigrationError
from core.project_v2_schema import ProjectV2ValidationError, validate_project_v2


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


class ProjectV2ServiceTests(unittest.TestCase):
    def test_create_empty_project_has_review_first_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            descriptor = ProjectV2Service().create_project(tmp, "新故事", author="作者")

            self.assertEqual(descriptor.name, "新故事")
            metadata = json.loads((descriptor.root / "project.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], 2)
            self.assertEqual(metadata["content_policy"]["legacy_import"], "manuscript_only")
            self.assertTrue(metadata["content_policy"]["knowledge_requires_review"])
            for relative in (
                "knowledge/entities.json", "knowledge/relations.json",
                "knowledge/world_rules.json", "knowledge/timeline.json",
                "proposals/index.json", "provenance/imports.json",
                "provenance/evidence.json",
                "manuscript/index.json",
            ):
                self.assertTrue((descriptor.root / relative).is_file(), relative)

    def test_create_from_v1_plan_copies_only_normalized_manuscript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "old"
            output = Path(tmp) / "output"
            output.mkdir()
            shutil.copytree(FIXTURE_PROJECT, source)
            before = _digests(source)
            plan = ManuscriptImportService().scan(source)

            descriptor = ProjectV2Service().create_project(
                output,
                "重建项目",
                author="测试",
                import_plan=plan,
            )

            self.assertEqual(before, _digests(source))
            self.assertFalse((descriptor.root / "canon").exists())
            self.assertFalse((descriptor.root / "memory").exists())
            chapter = (descriptor.root / "manuscript" / "chapter_0001.md").read_text(encoding="utf-8")
            self.assertTrue(chapter.startswith("# 第一章 雾中的信\n"))
            self.assertIn("零点的雾笛", chapter)
            self.assertNotIn("## 大纲", chapter)
            self.assertNotIn("作者备注", chapter)
            index = json.loads((descriptor.root / "manuscript" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(len(index["chapters"]), 2)
            self.assertNotIn(str(source), json.dumps(index, ensure_ascii=False))

    def test_legacy_service_rejects_v2_without_modifying_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            descriptor = ProjectV2Service().create_project(tmp, "未来项目")
            before = _digests(descriptor.root)

            with self.assertRaises(ProjectMigrationError):
                ProjectService().open_project(descriptor.root)

            self.assertEqual(before, _digests(descriptor.root))

    def test_new_project_opens_without_normalization_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            descriptor = ProjectV2Service().create_project(tmp, "可重复打开项目")
            before = _digests(descriptor.root)

            reopened = ProjectV2Service.open_project(descriptor.root)

            self.assertEqual(reopened.project_id, descriptor.project_id)
            self.assertEqual(before, _digests(descriptor.root))

    def test_open_preserves_semantically_equivalent_json_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            descriptor = ProjectV2Service().create_project(tmp, "外部格式项目")
            entities = descriptor.root / "knowledge" / "entities.json"
            entities.write_text(
                '{"schema_version":1,"entities":[]}\r\n',
                encoding="utf-8",
                newline="",
            )
            before = _digests(descriptor.root)

            ProjectV2Service.open_project(descriptor.root)

            self.assertEqual(before, _digests(descriptor.root))

    def test_validation_rejects_missing_collection_without_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            descriptor = ProjectV2Service().create_project(tmp, "损坏项目")
            target = descriptor.root / "knowledge" / "entities.json"
            target.unlink()

            with self.assertRaises(ProjectV2ValidationError):
                validate_project_v2(descriptor.root)
            self.assertFalse(target.exists())

    def test_phase8_project_without_evidence_projection_opens_compatibly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            descriptor = ProjectV2Service().create_project(tmp, "阶段八项目")
            evidence = descriptor.root / "provenance" / "evidence.json"
            evidence.unlink()

            validate_project_v2(descriptor.root)
            reopened = ProjectV2Service.open_project(descriptor.root)

            self.assertEqual(reopened.project_id, descriptor.project_id)
            self.assertTrue(evidence.is_file())


if __name__ == "__main__":
    unittest.main()
