import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.app_paths import app_cache_dir, app_config_dir, update_cache_dir
from core.config import get_update_cache_path, load_config, normalize_config, save_config


class ApplicationPathTests(TestCase):
    def test_windows_uses_roaming_config_and_local_cache(self) -> None:
        with patch("core.app_paths.sys.platform", "win32"), patch.dict(
            os.environ,
            {
                "APPDATA": r"C:\Users\writer\AppData\Roaming",
                "LOCALAPPDATA": r"C:\Users\writer\AppData\Local",
            },
            clear=True,
        ):
            self.assertEqual(
                app_config_dir(),
                Path(r"C:\Users\writer\AppData\Roaming") / "Novalist",
            )
            self.assertEqual(
                app_cache_dir(),
                Path(r"C:\Users\writer\AppData\Local") / "Novalist",
            )
            self.assertEqual(update_cache_dir(), app_cache_dir() / "updates")
            self.assertEqual(get_update_cache_path(), update_cache_dir())

    def test_linux_honors_xdg_directories(self) -> None:
        with patch("core.app_paths.sys.platform", "linux"), patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": "/tmp/novalist-config",
                "XDG_CACHE_HOME": "/tmp/novalist-cache",
            },
            clear=True,
        ):
            self.assertEqual(app_config_dir(), Path("/tmp/novalist-config/Novalist"))
            self.assertEqual(app_cache_dir(), Path("/tmp/novalist-cache/Novalist"))


class ConfigMigrationTests(TestCase):
    def test_legacy_config_is_copied_normalized_and_retained(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "install" / "config.json"
            target = root / "profile" / "config.json"
            legacy.parent.mkdir()
            legacy.write_text(
                json.dumps(
                    {
                        "theme": "dark",
                        "continue_target_chars": 2600,
                        "auto_save_interval": 9999,
                    }
                ),
                encoding="utf-8",
            )

            with patch("core.config.get_config_path", return_value=target), patch(
                "core.config.get_legacy_config_path", return_value=legacy
            ):
                loaded = load_config()

            self.assertEqual(loaded["theme"], "dark")
            self.assertEqual(loaded["expand_target_chars"], 2600)
            self.assertEqual(loaded["auto_save_interval"], 600)
            self.assertTrue(legacy.is_file())
            persisted = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(persisted, loaded)
            self.assertNotIn("continue_target_chars", persisted)

    def test_new_config_takes_precedence_over_legacy_config(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "install" / "config.json"
            target = root / "profile" / "config.json"
            legacy.parent.mkdir()
            target.parent.mkdir()
            legacy.write_text('{"theme":"dark"}', encoding="utf-8")
            target.write_text('{"theme":"light"}', encoding="utf-8")

            with patch("core.config.get_config_path", return_value=target), patch(
                "core.config.get_legacy_config_path", return_value=legacy
            ):
                loaded = load_config()

            self.assertEqual(loaded["theme"], "light")

    def test_invalid_legacy_config_is_not_migrated(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "install" / "config.json"
            target = root / "profile" / "config.json"
            legacy.parent.mkdir()
            legacy.write_text("{invalid", encoding="utf-8")

            with patch("core.config.get_config_path", return_value=target), patch(
                "core.config.get_legacy_config_path", return_value=legacy
            ):
                loaded = load_config()

            self.assertEqual(loaded["theme"], "light")
            self.assertFalse(target.exists())

    def test_migration_write_failure_does_not_discard_loaded_settings(self) -> None:
        with TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "config.json"
            legacy.write_text('{"theme":"dark"}', encoding="utf-8")

            with patch(
                "core.config.get_config_path",
                return_value=Path(tmp) / "profile" / "config.json",
            ), patch(
                "core.config.get_legacy_config_path", return_value=legacy
            ), patch(
                "core.config._write_config", side_effect=OSError("read only")
            ):
                loaded = load_config()

            self.assertEqual(loaded["theme"], "dark")

    def test_save_config_creates_the_user_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "profile" / "config.json"
            with patch("core.config.get_config_path", return_value=target):
                save_config({"theme": "dark"})

            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"theme": "dark"},
            )

    def test_update_preferences_are_normalized(self) -> None:
        config = normalize_config(
            {
                "update_channel": "unsupported",
                "auto_check_updates": 1,
                "last_update_check_at": None,
                "skipped_update_version": None,
            }
        )
        self.assertEqual(config["update_channel"], "beta")
        self.assertTrue(config["auto_check_updates"])
        self.assertEqual(config["last_update_check_at"], "")
        self.assertEqual(config["skipped_update_version"], "")
