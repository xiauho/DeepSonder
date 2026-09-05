import os
from unittest import SkipTest, TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core.theme_tokens import LIGHT_COLORS  # noqa: E402
from ui.navigation import PrimaryNavigation  # noqa: E402
from ui.theme import apply_theme  # noqa: E402


class PrimaryNavigationStyleTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        instance = QCoreApplication.instance()
        if instance is not None and not isinstance(instance, QApplication):
            raise SkipTest("已有 QCoreApplication，无法在同一进程升级为 QApplication")
        cls.app = instance or QApplication([])

    @staticmethod
    def _child_colors(button) -> tuple[str, str]:
        return tuple(
            label.palette().color(label.foregroundRole()).name().upper()
            for label in (button._icon_label, button._text_label)
        )

    def test_theme_reload_does_not_leave_settings_label_active(self) -> None:
        navigation = PrimaryNavigation()
        self.addCleanup(navigation.close)
        navigation.show()
        apply_theme(self.app, {"theme": "light"})
        self.app.processEvents()
        inactive_settings_colors = self._child_colors(navigation.buttons["settings"])

        navigation.set_active("settings")
        apply_theme(self.app, {"theme": "light"})
        self.app.processEvents()
        self.assertEqual(
            self._child_colors(navigation.buttons["settings"]),
            (LIGHT_COLORS["accent_color"],) * 2,
        )

        navigation.set_active("dashboard")
        self.app.processEvents()

        self.assertEqual(
            self._child_colors(navigation.buttons["settings"]),
            inactive_settings_colors,
        )
        self.assertEqual(
            self._child_colors(navigation.buttons["dashboard"]),
            (LIGHT_COLORS["accent_color"],) * 2,
        )
