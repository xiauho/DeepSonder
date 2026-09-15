import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QObject, Signal

from ui.settings_controller import SettingsController


class FakeDocumentController:
    def __init__(self):
        self.auto_save_calls = []

    def configure_auto_save(self, enabled, interval):
        self.auto_save_calls.append((enabled, interval))


class FakeDSHTask(QObject):
    success = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, _fn, parent=None):
        super().__init__(parent)
        self.started = False

    def isRunning(self):  # noqa: N802 - mirrors QThread API
        return self.started

    def start(self):
        self.started = True
        self.success.emit("连接成功")
        self.started = False
        self.finished.emit()


class SettingsControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_apply_normalizes_persists_and_updates_auto_save(self) -> None:
        document_controller = FakeDocumentController()
        with patch("ui.settings_controller.save_config") as save_config, patch(
            "ui.settings_controller.apply_theme"
        ):
            controller = SettingsController(
                {"theme": "invalid", "auto_save_interval": 1},
                document_controller,
            )
            applied = controller.apply(
                {"theme": "dark", "auto_save": False, "auto_save_interval": 9999},
                message="已保存",
            )

        self.assertEqual(applied["theme"], "dark")
        self.assertFalse(applied["auto_save"])
        self.assertEqual(applied["auto_save_interval"], 600)
        self.assertEqual(document_controller.auto_save_calls[-1], (False, 600))
        save_config.assert_called_once()

    def test_toggle_theme_emits_config_change(self) -> None:
        document_controller = FakeDocumentController()
        controller = SettingsController({"theme": "light"}, document_controller)
        changes = []
        controller.config_changed.connect(lambda config, message: changes.append((config, message)))

        with patch("ui.settings_controller.save_config"), patch(
            "ui.settings_controller.apply_theme"
        ):
            controller.toggle_theme()

        self.assertEqual(changes[-1][0]["theme"], "dark")
        self.assertEqual(changes[-1][1], "主题已切换")

    def test_synchronize_discards_retired_update_metadata_without_saving(self) -> None:
        document_controller = FakeDocumentController()
        controller = SettingsController({"theme": "light"}, document_controller)

        with patch("ui.settings_controller.save_config") as save_config:
            controller.synchronize(
                {
                    "theme": "light",
                    "last_update_check_at": "2026-08-30T12:00:00Z",
                    "skipped_update_version": "v2.0.6-beta",
                }
            )

        self.assertNotIn("last_update_check_at", controller.config)
        self.assertNotIn("skipped_update_version", controller.config)
        save_config.assert_not_called()

    def test_dsh_test_task_is_exposed_through_controller_signals(self) -> None:
        document_controller = FakeDocumentController()
        controller = SettingsController({}, document_controller)
        events = []
        controller.connection_started.connect(lambda: events.append("started"))
        controller.connection_succeeded.connect(events.append)
        controller.connection_finished.connect(lambda: events.append("finished"))

        with patch("ui.settings_controller.DSHTask", FakeDSHTask):
            self.assertTrue(controller.test_dsh({"dsh_command": "dsh"}))

        self.assertEqual(events, ["started", "连接成功", "finished"])
