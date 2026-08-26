from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QMessageBox

from core.ai_protocol import ForeshadowingSuggestion
from ui.ai_result_coordinator import (
    AIResultCoordinator,
    build_foreshadowing_review_rows,
)


class AIResultCoordinatorTests(TestCase):
    def test_every_selected_foreshadowing_gets_a_review_row(self) -> None:
        rows = build_foreshadowing_review_rows(
            (
                ForeshadowingSuggestion(
                    "f-detected",
                    "正文证据",
                    "已经闭合",
                ),
            ),
            {
                "f-detected": "AI 命中的伏笔",
                "f-missed": "AI 未命中的伏笔",
            },
        )

        self.assertEqual([row.note_id for row in rows], ["f-detected", "f-missed"])
        self.assertIsNotNone(rows[0].suggestion)
        self.assertIsNone(rows[1].suggestion)

    def test_expansion_cancel_does_not_replace_body(self) -> None:
        replace_body = Mock()
        dialog = SimpleNamespace(confirmed=False, exec=Mock())
        with patch("ui.ai_result_coordinator.ExpansionPreviewDialog", return_value=dialog):
            outcome = AIResultCoordinator().confirm_expansion(
                text="正文",
                char_count=2,
                length_ok=True,
                has_existing_content=True,
                context_matches=lambda: True,
                replace_body=replace_body,
            )

        self.assertEqual(outcome.status, "cancelled")
        replace_body.assert_not_called()

    def test_expansion_stale_context_does_not_replace_body(self) -> None:
        replace_body = Mock()
        dialog = SimpleNamespace(confirmed=True, exec=Mock())
        with patch("ui.ai_result_coordinator.ExpansionPreviewDialog", return_value=dialog):
            with patch("ui.ai_result_coordinator.QMessageBox.warning"):
                outcome = AIResultCoordinator().confirm_expansion(
                    text="正文",
                    char_count=2,
                    length_ok=True,
                    has_existing_content=False,
                    context_matches=lambda: False,
                    replace_body=replace_body,
                )

        self.assertEqual(outcome.status, "stale")
        replace_body.assert_not_called()

    def test_expansion_returns_user_selected_foreshadowing_resolutions(self) -> None:
        replace_body = Mock()
        dialog = SimpleNamespace(
            confirmed=True,
            exec=Mock(),
            selected_resolution_ids=lambda: ("f-selected",),
        )
        with patch("ui.ai_result_coordinator.ExpansionPreviewDialog", return_value=dialog):
            outcome = AIResultCoordinator().confirm_expansion(
                text="正文",
                char_count=2,
                length_ok=True,
                has_existing_content=False,
                context_matches=lambda: True,
                replace_body=replace_body,
            )

        self.assertEqual(outcome.status, "committed")
        self.assertEqual(outcome.value, ("f-selected",))
        replace_body.assert_called_once_with("正文")

    def test_memory_cancel_and_stale_context_do_not_commit(self) -> None:
        commit = Mock()
        coordinator = AIResultCoordinator()
        with patch(
            "ui.ai_result_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            cancelled = coordinator.confirm_memory(
                summary="摘要",
                context_matches=lambda: True,
                commit=commit,
            )
        self.assertEqual(cancelled.status, "cancelled")

        with patch(
            "ui.ai_result_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            with patch("ui.ai_result_coordinator.QMessageBox.warning"):
                stale = coordinator.confirm_memory(
                    summary="摘要",
                    context_matches=lambda: False,
                    commit=commit,
                )
        self.assertEqual(stale.status, "stale")
        commit.assert_not_called()
