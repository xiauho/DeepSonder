import unittest
from pathlib import Path
from unittest.mock import Mock

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


class MainWindowActionButtonTests(unittest.TestCase):
    def test_action_bar_uses_stable_and_consistent_labels(self) -> None:
        self.assertEqual(
            MainWindow.ACTION_BUTTON_LABELS,
            {
                "new_chapter": "新建章节",
                "open_project": "打开项目",
                "save": "保存",
                "export": "导出",
                "focus": "专注模式",
                "check": "一致性检查",
                "memory": "更新故事记忆",
                "continue": "AI 扩写",
            },
        )
        self.assertEqual(
            set(MainWindow.ACTION_BUTTON_LABELS),
            set(MainWindow.ACTION_ICONS) - {"delete_chapter"},
        )

    def test_action_state_sync_does_not_replace_the_button_label(self) -> None:
        action = Mock()
        action.isEnabled.return_value = False
        action.text.return_value = "一致性检查"
        button = Mock()

        MainWindow._sync_action_button(action, button)

        button.setEnabled.assert_called_once_with(False)
        button.setToolTip.assert_called_once_with("一致性检查")
        button.set_label.assert_not_called()


if __name__ == "__main__":
    unittest.main()
