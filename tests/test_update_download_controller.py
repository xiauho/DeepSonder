from pathlib import Path
from unittest import TestCase

from PySide6.QtCore import QCoreApplication, QObject, Signal

from core.update_download_service import (
    ArchiveInspection,
    ManifestAsset,
    UpdateManifest,
    VerifiedUpdate,
)
from core.update_service import UpdateInfo
from core.version import AppVersion
from ui.update_download_controller import UpdateDownloadController


VERSION = AppVersion.parse("2.0.7-beta")


def release_info() -> UpdateInfo:
    return UpdateInfo(
        version=VERSION,
        tag_name="v2.0.7-beta",
        title="v2.0.7-beta",
        notes="",
        published_at="2026-08-31T00:00:00Z",
        release_url="https://github.com/xiauho/novalist/releases/tag/v2.0.7-beta",
        prerelease=True,
    )


def verified_update() -> VerifiedUpdate:
    manifest = UpdateManifest(
        schema_version=2,
        version=VERSION,
        channel="beta",
        platform="windows",
        architecture="x64",
        asset=ManifestAsset("Novalist-v2.0.7-beta-windows-x64.zip", 10, "a" * 64),
        minimum_updater_version=VERSION,
        published_at="2026-08-31T00:00:00Z",
    )
    return VerifiedUpdate(
        release=release_info(),
        manifest=manifest,
        archive_path=Path("update.verified.zip"),
        inspection=ArchiveInspection(file_count=3, expanded_size=30),
    )


class FakeTask(QObject):
    success = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
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
        except Exception as exc:  # noqa: BLE001 - test task mirrors DSHTask
            self.failed.emit(str(exc))
        else:
            self.success.emit(result)
        self._running = False
        self.finished.emit()

    def cancel(self):
        self.cancelled.emit()


class UpdateDownloadControllerTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_download_emits_progress_and_verified_result(self) -> None:
        expected = verified_update()

        def downloader(_release, **kwargs):
            kwargs["progress"](type("Progress", (), {"downloaded": 5, "total": 10})())
            return expected

        controller = UpdateDownloadController(
            task_factory=FakeTask,
            downloader=downloader,
        )
        events = []
        controller.started.connect(lambda release: events.append(("started", release.tag_name)))
        controller.progress_changed.connect(
            lambda downloaded, total: events.append(("progress", downloaded, total))
        )
        controller.succeeded.connect(lambda result: events.append(("success", result)))
        controller.finished.connect(lambda: events.append(("finished",)))

        self.assertTrue(controller.download(release_info(), VERSION))

        self.assertEqual(events[0], ("started", "v2.0.7-beta"))
        self.assertEqual(events[1], ("progress", 5, 10))
        self.assertEqual(events[2], ("success", expected))
        self.assertEqual(events[3], ("finished",))

    def test_downloader_failure_is_presented(self) -> None:
        controller = UpdateDownloadController(
            task_factory=FakeTask,
            downloader=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("校验失败")
            ),
        )
        errors = []
        controller.failed.connect(errors.append)

        self.assertTrue(controller.download(release_info(), VERSION))

        self.assertEqual(errors, ["校验失败"])
