"""Capture pointer and keyboard focus states using a temporary empty workbench."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from core.config import DEFAULT_CONFIG
from core.theme_tokens import LIGHT_COLORS, DARK_COLORS
from main import _configure_application
from tests.test_window_control_focus import has_focus_border
from ui.main_window import MainWindow

app = QApplication([])
_configure_application(app)
out = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as temporary, patch.object(MainWindow, "_restore_last_project"):
    window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False},
                        ui_state_path=Path(temporary) / "ui-state.json")
    window.resize(1100, 720)
    window.show()
    window.window_state_controller.activate_route("writing")
    for theme, palette in (("light", LIGHT_COLORS), ("dark", DARK_COLORS)):
        window.appearance_controller.apply({**window.config, **palette, "theme": theme})
        window.activateWindow()
        assert QTest.qWaitForWindowActive(window, 1000)
        window.editor.text_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.mouseClick(window.title_bar.minimize_button, Qt.MouseButton.LeftButton)
        window.showNormal()
        window.activateWindow()
        assert QTest.qWaitForWindowActive(window, 1000)
        app.processEvents()
        assert window.focusWidget() is window.editor.text_edit
        assert not has_focus_border(window.title_bar.minimize_button)
        assert window.title_bar.grab().save(str(out / f"mouse-restored-{theme}.png"))
        window.title_bar.maximize_button.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(window.title_bar.maximize_button, Qt.Key.Key_Backtab)
        app.processEvents()
        assert has_focus_border(window.title_bar.minimize_button)
        assert window.title_bar.grab().save(str(out / f"keyboard-{theme}.png"))
    window.close()
print("Pointer and keyboard captures verified in both themes.")
