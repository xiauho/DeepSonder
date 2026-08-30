from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QObject, Signal

from core.update_service import UpdateCheckResult, UpdateInfo
from core.version import AppVersion
from ui.update_controller import UpdateController, should_auto_check


def available_result() -> UpdateCheckResult:
    return UpdateCheckResult(
        current_version=AppVersion.parse("2.0.5-beta"),
        latest=UpdateInfo(
            version=AppVersion.parse("2.0.6-beta"),
            tag_name="v2.0.6-beta",
            title="v2.0.6-beta",
            notes="Update notes",
            published_at="2026-08-30T00:00:00Z",
            release_url="https://github.com/xiauho/novalist/releases/tag/v2.0.6-beta",
            prerelease=True,
        ),
    )


class FakeTask(QObject):
    success = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._running = False

    def isRunning(self):  # noqa: N802 - mirrors QThread
        return self._running

    def start(self):
        self._running = True
        try:
            result = self._fn(None)
        except Exception as exc:  # noqa: BLE001 - mirror background task
            self.failed.emit(str(exc))
        else:
            self.success.emit(result)
        self._running = False
        self.finished.emit()


class UpdateScheduleTests(TestCase):
    def test_auto_check_requires_opt_in(self) -> None:
        self.assertFalse(should_auto_check({"auto_check_updates": False}))
        self.assertTrue(should_auto_check({"auto_check_updates": True}))

    def test_auto_check_is_limited_to_once_per_day(self) -> None:
        now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        recent = (now - timedelta(hours=23)).isoformat()
        old = (now - timedelta(hours=25)).isoformat()
        self.assertFalse(
            should_auto_check(
                {"auto_check_updates": True, "last_update_check_at": recent},
                now,
            )
        )
        self.assertTrue(
            should_auto_check(
                {"auto_check_updates": True, "last_update_check_at": old},
                now,
            )
        )

    def test_invalid_timestamp_is_treated_as_due(self) -> None:
        self.assertTrue(
            should_auto_check(
                {"auto_check_updates": True, "last_update_check_at": "invalid"}
            )
        )


class UpdateControllerTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_manual_check_emits_result_and_persists_timestamp(self) -> None:
        now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
        controller = UpdateController(
            {"update_channel": "beta"},
            task_factory=FakeTask,
            clock=lambda: now,
        )
        events = []
        controller.started.connect(lambda manual: events.append(("started", manual)))
        controller.result_ready.connect(
            lambda result, manual: events.append((result.latest.tag_name, manual))
        )
        controller.finished.connect(lambda manual: events.append(("finished", manual)))

        with patch(
            "ui.update_controller.load_current_version",
            return_value=AppVersion.parse("2.0.5-beta"),
        ), patch(
            "ui.update_controller.check_for_updates",
            return_value=available_result(),
        ), patch("ui.update_controller.save_config") as save:
            self.assertTrue(controller.check(manual=True))

        self.assertEqual(
            events,
            [("started", True), ("v2.0.6-beta", True), ("finished", True)],
        )
        self.assertEqual(controller.config["last_update_check_at"], "2026-08-30T12:00:00Z")
        save.assert_called_once()

    def test_automatic_check_respects_schedule(self) -> None:
        controller = UpdateController(
            {"auto_check_updates": False},
            task_factory=FakeTask,
        )
        with patch.object(controller, "check") as check:
            self.assertFalse(controller.check_automatically())
        check.assert_not_called()

    def test_skipped_version_is_hidden_only_for_automatic_checks(self) -> None:
        controller = UpdateController(
            {"skipped_update_version": "v2.0.6-beta"},
            task_factory=FakeTask,
        )
        result = available_result()
        self.assertFalse(controller.should_present(result, manual=False))
        self.assertTrue(controller.should_present(result, manual=True))

    def test_skip_version_is_persisted_and_emitted(self) -> None:
        controller = UpdateController({}, task_factory=FakeTask)
        changes = []
        controller.config_changed.connect(changes.append)
        with patch("ui.update_controller.save_config") as save:
            controller.skip_version("v2.0.6-beta")
        self.assertEqual(controller.config["skipped_update_version"], "v2.0.6-beta")
        self.assertEqual(changes[-1]["skipped_update_version"], "v2.0.6-beta")
        save.assert_called_once()

    def test_manual_version_read_failure_is_reported(self) -> None:
        controller = UpdateController({}, task_factory=FakeTask)
        errors = []
        controller.failed.connect(lambda message, manual: errors.append((message, manual)))
        with patch(
            "ui.update_controller.load_current_version",
            side_effect=ValueError("无法读取当前版本信息。"),
        ):
            self.assertFalse(controller.check(manual=True))
        self.assertEqual(errors, [("无法读取当前版本信息。", True)])
