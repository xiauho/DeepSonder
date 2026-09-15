import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.app_paths import app_cache_dir, app_config_dir, update_cache_dir
from core.config import (
    get_chapter_target_chars,
    get_update_cache_path,
    load_config,
    normalize_config,
    save_config,
)


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
                Path(r"C:\Users\writer\AppData\Roaming") / "DeepSonder" / "Electron",
            )
            self.assertEqual(
                app_cache_dir(),
                Path(r"C:\Users\writer\AppData\Local") / "DeepSonder" / "Electron",
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
            self.assertEqual(app_config_dir(), Path("/tmp/novalist-config/DeepSonder/Electron"))
            self.assertEqual(app_cache_dir(), Path("/tmp/novalist-cache/DeepSonder/Electron"))


class ConfigMigrationTests(TestCase):
    def test_chapter_target_accessor_is_the_only_runtime_fallback(self) -> None:
        self.assertEqual(get_chapter_target_chars({"chapter_target_chars": 4200}), 4200)
        self.assertEqual(get_chapter_target_chars({}), 3000)
        self.assertEqual(get_chapter_target_chars({"chapter_target_chars": "bad"}), 3000)
        self.assertEqual(get_chapter_target_chars({"chapter_target_chars": 99}), 300)
        self.assertEqual(get_chapter_target_chars({"chapter_target_chars": 50_000}), 10_000)

    def test_schema_five_expansion_target_becomes_chapter_target(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "profile" / "config.json"
            target.parent.mkdir()
            target.write_text(
                json.dumps(
                    {
                        "config_schema_version": 5,
                        "expand_target_chars": 4200,
                    }
                ),
                encoding="utf-8",
            )

            with patch("core.config.get_config_path", return_value=target):
                loaded = load_config()

            self.assertEqual(loaded["chapter_target_chars"], 4200)
            self.assertNotIn("expand_target_chars", loaded)
            self.assertEqual(loaded["config_schema_version"], 6)

    def test_missing_series_config_starts_from_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "DeepSonder" / "Electron" / "config.json"
            old_install_config = root / "old-install" / "config.json"
            old_install_config.parent.mkdir()
            old_install_config.write_text('{"theme":"dark"}', encoding="utf-8")

            with patch("core.config.get_config_path", return_value=target):
                loaded = load_config()

            self.assertEqual(loaded["theme"], "light")
            self.assertFalse(target.exists())
            self.assertTrue(old_install_config.is_file())

    def test_save_config_creates_the_user_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "profile" / "config.json"
            with patch("core.config.get_config_path", return_value=target):
                save_config({"theme": "dark"})

            saved = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(saved["theme"], "dark")
            self.assertEqual(saved["config_schema_version"], 6)

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

    def test_future_config_schema_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "高于当前程序支持"):
            normalize_config({"config_schema_version": 999})

    def test_config_schema_migration_is_idempotent(self) -> None:
        first = normalize_config(
            {
                "continue_target_chars": 2600,
                "dsh_prompt_transport": "auto",
                "ai_memory_pipeline": "legacy",
            }
        )
        self.assertEqual(normalize_config(first), first)
        self.assertEqual(first["chapter_target_chars"], 2600)
        self.assertNotIn("dsh_prompt_transport", first)
        self.assertNotIn("ai_memory_pipeline", first)
