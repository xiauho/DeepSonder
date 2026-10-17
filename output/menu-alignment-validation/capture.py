"""Disposable layout capture for this change; no real projects are opened."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ["QT_SCREEN_SCALE_FACTORS"] = "1"
scale = sys.argv[1]
os.environ["QT_SCALE_FACTOR"] = str(float(scale) / float(sys.argv[2]))

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication
from core.config import DEFAULT_CONFIG
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from main import _configure_application
from ui.main_window import MainWindow

app = QApplication([])
_configure_application(app)
out = Path(__file__).resolve().parent
with tempfile.TemporaryDirectory() as temporary, patch.object(MainWindow, "_restore_last_project"):
    window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False},
                        ui_state_path=Path(temporary) / "ui-state.json")
    window.resize(1100, 720)
    window.setWindowTitle("DeepSonder-PySide6 — 示例小说 · 青云之上")
    window.show()
    app.processEvents()
    for font in (14, 22):
        for theme in ("light", "dark"):
            window.appearance_controller.apply({**window.config, **(DARK_COLORS if theme == "dark" else LIGHT_COLORS), "theme": theme, "ui_font_size": font})
            for route in ("dashboard", "writing", "focus"):
                window.window_state_controller.activate_route("writing" if route == "focus" else route)
                if route == "focus":
                    window.toggle_focus_mode()
                for _ in range(3):
                    app.processEvents()
                bar = window.title_bar
                assert window.width() == 1100, (scale, font, theme, route, window.size())
                assert bar.close_button.geometry().right() == window.width() - 1
                assert bar.rect().contains(bar.minimize_button.geometry())
                assert bar.rect().contains(bar.maximize_button.geometry())
                assert bar.close_button.isVisible()
                assert (bar.close_button.width(), bar.close_button.height()) == (46, 44)
                if route != "focus":
                    for action in bar.menu_bar.actions():
                        item_center = bar.menu_bar.mapTo(bar, bar.menu_bar.actionGeometry(action).center())
                        assert abs(item_center.y() - bar.brand.geometry().center().y()) <= 1
                        assert not bar.menu_bar.actionGeometry(action).isEmpty()
                        assert bar.menu_bar.rect().contains(bar.menu_bar.actionGeometry(action)), (scale, font, theme, route, action.text(), bar.menu_bar.rect(), bar.menu_bar.actionGeometry(action))
                filename = f"scale-{scale}-font-{font}-{theme}-{route}.png"
                assert window.grab().save(str(out / filename))
                assert bar.grab().save(str(out / filename.replace(".png", "-topbar.png")))
                if route == "focus":
                    window._exit_focus_mode()
    native_style = window.window_chrome.native_frame.get_style(int(window.winId()), -16)
    window.close()
print(f"Scale {scale}, pixel ratio {window.devicePixelRatioF()}, native style {native_style:#x}: 12 native layout captures and containment checks passed")
