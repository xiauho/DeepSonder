import tempfile
import unittest
from pathlib import Path

from application.project_service import ProjectService
from core.project import NovelProject
from core.project_schema import PROJECT_MANIFEST_RELATIVE_PATH


class ProjectServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ProjectService()

    def test_create_and_open_project_without_ui_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            opened = self.service.create_project(tmp, "测试 项目", author="作者")

            self.assertEqual(opened.project.name, "测试 项目")
            self.assertEqual(opened.project.meta["author"], "作者")
            self.assertEqual(opened.project.root.name, "测试 项目")
            self.assertIs(opened.data_store.project, opened.project)
            self.assertFalse(opened.migration.migrated)

            reopened = self.service.open_project(opened.project.root)
            self.assertEqual(reopened.project.root, opened.project.root)
            self.assertFalse(reopened.migration.migrated)

    def test_open_migrates_legacy_project_before_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "legacy", "旧项目")
            project.style_guide_path.unlink()
            (project.root / PROJECT_MANIFEST_RELATIVE_PATH).unlink()

            opened = self.service.open_project(project.root)

            self.assertTrue(opened.migration.migrated)
            self.assertTrue(opened.project.style_guide_path.is_file())
            self.assertTrue(
                (opened.project.root / PROJECT_MANIFEST_RELATIVE_PATH).is_file()
            )

    def test_create_rejects_existing_target_and_invalid_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.service.create_project(tmp, "重复项目")
            with self.assertRaises(FileExistsError):
                self.service.create_project(tmp, "重复项目")
            with self.assertRaises(NotADirectoryError):
                self.service.create_project(Path(tmp) / "missing", "项目")

    def test_recent_projects_are_normalized_deduplicated_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            roots = []
            for index in range(10):
                roots.append(
                    NovelProject.create(
                        Path(tmp) / f"project_{index}", f"项目 {index}"
                    ).root
                )
            config = {
                "recent_projects": [
                    str(roots[0]),
                    str(roots[0]),
                    str(Path(tmp) / "missing"),
                    *(str(path) for path in roots[1:]),
                ],
                "last_project": str(Path(tmp) / "missing"),
            }

            result = self.service.recent_projects(config)

            self.assertTrue(result.changed)
            self.assertEqual(result.paths, tuple(roots[:8]))
            self.assertEqual(config["recent_projects"], [str(path) for path in roots[:8]])
            self.assertEqual(config["last_project"], "")

    def test_remember_project_moves_it_to_front(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = NovelProject.create(Path(tmp) / "first", "一").root
            second = NovelProject.create(Path(tmp) / "second", "二").root
            config = {
                "recent_projects": [str(first), str(second)],
                "last_project": str(first),
            }

            changed = self.service.remember_project(config, second)

            self.assertTrue(changed)
            self.assertEqual(config["last_project"], str(second))
            self.assertEqual(config["recent_projects"], [str(second), str(first)])

    def test_recent_projects_reports_invalid_config_shape_for_persistence(self) -> None:
        config = {"recent_projects": "not-a-list", "last_project": ""}

        result = self.service.recent_projects(config)

        self.assertTrue(result.changed)
        self.assertEqual(result.paths, ())
        self.assertEqual(config["recent_projects"], [])


if __name__ == "__main__":
    unittest.main()
