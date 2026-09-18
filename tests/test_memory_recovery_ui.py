"""Exercise the actual recovery and confirmation layouts with temporary data."""
from unittest import TestCase
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from PySide6.QtGui import QFontDatabase
from core.task_controller import AITaskToken
from ui.ai_result_coordinator import MemoryPreviewDialog
from ui.theme import apply_theme
from test_memory_recovery import mixed_proposal


class MemoryRecoveryLayoutTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # Offscreen Windows Qt may not discover system font fallbacks.
        from pathlib import Path
        for name in ("msyh.ttc", "msyhbd.ttc"):
            font = Path("C:/Windows/Fonts") / name
            if font.exists():
                QFontDatabase.addApplicationFont(str(font))

    def assert_buttons_fit(self, dialog):
        from PySide6.QtCore import QRect
        buttons = [b for b in dialog.findChildren(QPushButton) if b.isVisible()]
        rectangles = [QRect(b.mapTo(dialog, b.rect().topLeft()), b.size()) for b in buttons]
        for index, rect in enumerate(rectangles):
            self.assertTrue(dialog.rect().contains(rect))
            for other in rectangles[index+1:]:
                self.assertFalse(rect.intersects(other))
        screen = self.app.primaryScreen().availableGeometry()
        self.assertLessEqual(dialog.width(), screen.width())
        self.assertLessEqual(dialog.height(), screen.height())

    def test_narrow_recovery_and_partial_preview_in_both_themes(self):
        import os
        from pathlib import Path
        from test_deferred_ai_review import DeferredAIReviewTests
        fixture = DeferredAIReviewTests(); fixture.setUp()
        proposal, state = mixed_proposal()
        project = Mock(); project.load_story_state.return_value = state
        fixture.session._project = project
        fixture.workflow._task_context_matches = Mock(return_value=True)
        for theme in ("light", "dark"):
            apply_theme(self.app, {"theme":theme, "ui_font_size":18})
            def inspect(box):
                box.show(); self.app.processEvents()
                self.assert_buttons_fit(box)
                output = os.environ.get("MEMORY_UI_CAPTURE_DIR")
                if output:
                    box.grab().save(str(Path(output) / ("memory-recovery-" + theme + ".png")))
                box.close()
                return 0
            with patch.object(QMessageBox, 'exec', inspect):
                fixture.workflow._on_memory_done(AITaskToken('memory', 'chapter_01'), proposal)
            preview = MemoryPreviewDialog("（部分记忆）林舟得到铜钱。", "未采用的更新：位置 → 北港\n" * 40, 1, 1)
            preview.resize(min(620, self.app.primaryScreen().availableGeometry().width()),
                           min(480, self.app.primaryScreen().availableGeometry().height()))
            preview.show(); self.app.processEvents()
            self.assert_buttons_fit(preview)
            self.assertGreater(preview.preview.verticalScrollBar().maximum(), 0)
            self.assertFalse(preview.confirmed)
            preview.close()
        apply_theme(self.app, {"theme":"light"})
