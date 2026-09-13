import shutil
import tempfile
import unittest
from pathlib import Path

from application.preferences_service import PreferencesService
from core.config import DEFAULT_CONFIG


FIXTURE_PROJECT = (
    Path(__file__).parent
    / "fixtures"
    / "electron_migration"
    / "golden_project"
)


class PreferencesServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved: list[dict] = []
        self.config = dict(DEFAULT_CONFIG)
        self.config["recent_projects"] = []
        self.service = PreferencesService(
            config_loader=lambda: self.config,
            config_saver=lambda value: self.saved.append(dict(value)),
        )

    def test_snapshot_exposes_only_frontend_preferences(self) -> None:
        snapshot = self.service.snapshot()

        self.assertEqual(snapshot.theme, "light")
        self.assertTrue(snapshot.auto_save)
        self.assertEqual(snapshot.auto_save_interval, 30)
        self.assertFalse(hasattr(snapshot, "dsh_command"))

    def test_update_is_allowlisted_and_normalized(self) -> None:
        snapshot = self.service.update(
            {"theme": "dark", "editor_font_size": 100, "auto_save_interval": 1}
        )

        self.assertEqual(snapshot.theme, "dark")
        self.assertEqual(snapshot.editor_font_size, 36)
        self.assertEqual(snapshot.auto_save_interval, 5)
        self.assertEqual(self.saved[-1]["theme"], "dark")
        with self.assertRaisesRegex(ValueError, "不可修改"):
            self.service.update({"dsh_command": "other"})
        with self.assertRaisesRegex(ValueError, "布尔值"):
            self.service.update({"auto_save": "false"})
        with self.assertRaisesRegex(ValueError, "整数"):
            self.service.update({"editor_font_size": True})

    def test_remember_and_restore_project_uses_existing_config_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "project"
            shutil.copytree(FIXTURE_PROJECT, copied)

            snapshot = self.service.remember_project(copied)
            self.config = self.saved[-1]

            self.assertEqual(snapshot.last_project, str(copied.resolve()))
            self.assertEqual(snapshot.recent_projects, (str(copied.resolve()),))
            self.assertEqual(self.service.last_project(), copied.resolve())


if __name__ == "__main__":
    unittest.main()
