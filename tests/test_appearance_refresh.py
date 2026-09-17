import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtGui import QPalette, QFontDatabase
from core.config import DEFAULT_CONFIG, normalize_config, load_config
from core.theme_tokens import LIGHT_COLORS, DARK_COLORS, PREVIOUS_LIGHT_COLORS, PREVIOUS_DARK_COLORS
from ui.theme import apply_theme
from ui.icons import IconTextButton
from ui.navigation import PrimaryNavigation
from ui.main_window import MainWindow


def contrast(a, b):
    def luminance(color):
        channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
        return sum(v * w for v, w in zip(linear, (.2126, .7152, .0722)))
    x, y = sorted((luminance(a), luminance(b)))
    return (y + .05) / (x + .05)


class AppearanceRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.font_id = -1
        if Path("C:/Windows/Fonts/msyh.ttc").exists():
            cls.font_id = QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        stylesheet = self.app.styleSheet()
        palette = self.app.palette()
        self.addCleanup(self.app.setStyleSheet, stylesheet)
        self.addCleanup(self.app.setPalette, palette)

    def test_default_palettes_have_readable_interactions(self):
        for colors in (LIGHT_COLORS, DARK_COLORS):
            for surface in ('background_color', 'panel_color', 'field_color', 'sidebar_color', 'popover_color', 'selection_color'):
                for foreground in ('text_color', 'muted_text_color', 'accent_color'):
                    with self.subTest(surface=surface, foreground=foreground, theme=colors['background_color']):
                        self.assertGreaterEqual(contrast(colors[foreground], colors[surface]), 4.5)
            for surface in ('primary_color', 'primary_hover_color'):
                self.assertGreaterEqual(contrast(colors['primary_text_color'], colors[surface]), 4.5)
            self.assertGreaterEqual(contrast(colors['control_border_color'], colors['field_color']), 3)

    def test_existing_stock_presets_update_and_custom_palettes_survive(self):
        for theme, previous, current in (('light', PREVIOUS_LIGHT_COLORS, LIGHT_COLORS), ('dark', PREVIOUS_DARK_COLORS, DARK_COLORS)):
            config = normalize_config({**DEFAULT_CONFIG, **previous, 'theme': theme})
            for key in previous:
                self.assertEqual(config[key], current[key])
            custom = {**previous, 'accent_color': '#8344AA'}
            config = normalize_config({**DEFAULT_CONFIG, **custom, 'theme': theme})
            for key in previous:
                self.assertEqual(config[key], custom[key])

    def test_saved_dark_profile_gets_dark_defaults_for_new_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps({'theme': 'dark', **PREVIOUS_DARK_COLORS}), encoding='utf-8')
            with patch('core.config.get_config_path', return_value=path):
                loaded = load_config()
            for key, value in DARK_COLORS.items():
                self.assertEqual(loaded[key], value)

    def test_theme_switch_updates_navigation_buttons_and_selection(self):
        nav = PrimaryNavigation()
        button = IconTextButton('check', '确认')
        button.setObjectName('accentButton')
        self.addCleanup(nav.close)
        self.addCleanup(button.close)
        nav.resize(76, 700)
        nav.show()
        button.show()
        self.assertIsNone(nav.findChild(QLabel, 'brandTitle'))
        self.assertIs(nav.layout().itemAt(0).widget(), nav.quick_open_button)
        self.assertEqual(nav.quick_open_button.y(), 14)
        for theme, colors in (('light', LIGHT_COLORS), ('dark', DARK_COLORS), ('light', LIGHT_COLORS)):
            apply_theme(self.app, {'theme': theme})
            nav.set_active('memory')
            button.setEnabled(True)
            self.app.processEvents()
            self.assertEqual(nav.grab().toImage().pixelColor(2, 2).name().upper(), colors['sidebar_color'])
            for label in (nav.buttons['memory']._text_label, nav.buttons['memory']._icon_label):
                self.assertEqual(label.palette().color(label.foregroundRole()).name().upper(), colors['accent_color'])
            self.assertEqual(button._text_label.palette().color(QPalette.ColorRole.WindowText).name().upper(), colors['primary_text_color'])
            self.assertEqual(self.app.palette().color(QPalette.ColorRole.HighlightedText).name().upper(), colors['primary_text_color'])
            button.setEnabled(False)
            self.app.processEvents()
            self.assertEqual(button._text_label.palette().color(QPalette.ColorRole.WindowText).name().upper(), colors['muted_text_color'])

    def test_narrow_workspace_layout_in_both_themes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(MainWindow, '_restore_last_project'):
            window = MainWindow(config={**DEFAULT_CONFIG, 'auto_save': False}, ui_state_path=Path(directory) / 'ui.json')
            try:
                window.window_state_controller.activate_route('writing')
                window.resize(1100, 720)
                window.show()
                for theme, colors in (('light', LIGHT_COLORS), ('dark', DARK_COLORS)):
                    apply_theme(self.app, {'theme': theme, **colors, 'ui_font_size': 18})
                    self.app.processEvents()
                    self.assertEqual(window.width(), 1100)
                    self.assertGreaterEqual(window.editor.width(), 650)
                    for button in [window.primary_nav.quick_open_button, *window.primary_nav.buttons.values()]:
                        self.assertTrue(window.primary_nav.rect().contains(button.geometry()))
                        self.assertTrue(button.rect().contains(button._text_label.geometry()))
                        self.assertGreaterEqual(button._text_label.width(), button._text_label.fontMetrics().horizontalAdvance(button._text_label.text()))
            finally:
                window.close()
