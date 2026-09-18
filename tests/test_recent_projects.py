import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.project import NovelProject
from application.project_service import ProjectService


class RecentProjectPathTests(unittest.TestCase):
    def test_valid_project_path_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = NovelProject.create(Path(tmp) / "proj", "测试").root
            self.assertEqual(ProjectService().safe_project_path(root), root.resolve())

    def test_missing_or_inaccessible_path_is_ignored(self) -> None:
        self.assertIsNone(ProjectService().safe_project_path("C:/missing-project"))
        with patch.object(Path, "is_dir", side_effect=PermissionError("拒绝访问")):
            self.assertIsNone(ProjectService().safe_project_path("C:/restricted-project"))
