import unittest
from pathlib import Path

from ui.main_window import MainWindow, fallback_chapter_after_delete
from ui.navigation import PrimaryNavigation


class MainWindowDeleteBehaviorTests(unittest.TestCase):
    def test_non_current_delete_does_not_choose_a_fallback(self) -> None:
        chapters = [Path("chapter_01.md"), Path("chapter_02.md"), Path("chapter_03.md")]

        self.assertIsNone(fallback_chapter_after_delete(chapters, 1, False))

    def test_current_delete_chooses_next_then_previous(self) -> None:
        chapters = [Path("chapter_01.md"), Path("chapter_02.md"), Path("chapter_03.md")]

        self.assertEqual(
            fallback_chapter_after_delete(chapters, 1, True),
            Path("chapter_03.md"),
        )
        self.assertEqual(
            fallback_chapter_after_delete(chapters, 2, True),
            Path("chapter_02.md"),
        )
        self.assertIsNone(fallback_chapter_after_delete([chapters[0]], 0, True))

    def test_delete_shortcut_does_not_overlap_editor_word_delete(self) -> None:
        self.assertEqual(MainWindow.DELETE_CHAPTER_SHORTCUT, "Ctrl+Shift+Delete")

    def test_trash_is_a_navigation_action_not_a_workspace_route(self) -> None:
        self.assertTrue(hasattr(PrimaryNavigation, "trash_requested"))
        self.assertNotIn("trash", {route for route, _icon, _label in PrimaryNavigation.ROUTES})


if __name__ == "__main__":
    unittest.main()
