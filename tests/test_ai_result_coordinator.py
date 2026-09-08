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

    def test_expansion_commits_the_candidate_selected_in_preview(self) -> None:
        replace_body = Mock()
        dialog = SimpleNamespace(
            confirmed=True,
            exec=Mock(),
            selected_text=lambda: "原稿",
            selected_resolution_ids=lambda: (),
        )
        with patch("ui.ai_result_coordinator.ExpansionPreviewDialog", return_value=dialog):
            outcome = AIResultCoordinator().confirm_expansion(
                text="纠偏稿",
                char_count=3,
                length_ok=True,
                has_existing_content=False,
                context_matches=lambda: True,
                replace_body=replace_body,
            )

        self.assertEqual(outcome.status, "committed")
        replace_body.assert_called_once_with("原稿")

    def test_continuation_commits_only_after_confirmation_and_fresh_context(self) -> None:
        append_body = Mock()
        continuation_text = "续" * 1900
        dialog = SimpleNamespace(confirmed=True, exec=Mock())
        with patch(
            "ui.ai_result_coordinator.ContinuationPreviewDialog",
            return_value=dialog,
        ):
            outcome = AIResultCoordinator().confirm_continuation(
                text=continuation_text,
                current_tail="原文结尾",
                current_chars=1000,
                requested_chars=2000,
                generated_chars=1900,
                target_chapter_chars=3000,
                length_ok=True,
                context_matches=lambda: True,
                append_body=append_body,
            )

        self.assertEqual(outcome.status, "committed")
        append_body.assert_called_once_with(continuation_text)

    def test_stale_continuation_is_not_appended(self) -> None:
        append_body = Mock()
        dialog = SimpleNamespace(confirmed=True, exec=Mock())
        with patch(
            "ui.ai_result_coordinator.ContinuationPreviewDialog",
            return_value=dialog,
        ), patch("ui.ai_result_coordinator.QMessageBox.warning"):
            outcome = AIResultCoordinator().confirm_continuation(
                text="续写正文",
                current_tail="原文结尾",
                current_chars=1000,
                requested_chars=2000,
                generated_chars=1900,
                target_chapter_chars=3000,
                length_ok=True,
                context_matches=lambda: False,
                append_body=append_body,
            )

        self.assertEqual(outcome.status, "stale")
        append_body.assert_not_called()

    def test_under_length_continuation_can_request_another_supplement(self) -> None:
        append_body = Mock()
        dialog = SimpleNamespace(
            confirmed=False,
            retry_requested=True,
            exec=Mock(),
            selected_text=lambda: "选中的偏短续写",
        )
        with patch(
            "ui.ai_result_coordinator.ContinuationPreviewDialog",
            return_value=dialog,
        ):
            outcome = AIResultCoordinator().confirm_continuation(
                text="偏短续写",
                current_tail="原文结尾",
                current_chars=800,
                requested_chars=2200,
                generated_chars=1500,
                target_chapter_chars=3000,
                run_target_chars=3000,
                length_ok=False,
                length_status="severely_under",
                context_matches=lambda: True,
                append_body=append_body,
            )

        self.assertEqual(outcome.status, "supplement_requested")
        self.assertEqual(outcome.value, "选中的偏短续写")
        append_body.assert_not_called()

    def test_severely_short_expansion_requires_explicit_second_confirmation(self) -> None:
        replace_body = Mock()
        dialog = SimpleNamespace(
            confirmed=True,
            retry_requested=False,
            exec=Mock(),
            selected_resolution_ids=lambda: (),
        )
        with patch(
            "ui.ai_result_coordinator.ExpansionPreviewDialog",
            return_value=dialog,
        ), patch(
            "ui.ai_result_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            outcome = AIResultCoordinator().confirm_expansion(
                text="短" * 2497,
                char_count=2497,
                target_chars=3000,
                length_ok=False,
                length_status="severely_under",
                has_existing_content=False,
                context_matches=lambda: True,
                replace_body=replace_body,
            )

        self.assertEqual(outcome.status, "cancelled")
        replace_body.assert_not_called()

    def test_memory_cancel_and_stale_context_do_not_commit(self) -> None:
        commit = Mock()
        coordinator = AIResultCoordinator()
        cancelled_dialog = SimpleNamespace(confirmed=False, exec=Mock())
        with patch(
            "ui.ai_result_coordinator.MemoryPreviewDialog",
            return_value=cancelled_dialog,
        ):
            cancelled = coordinator.confirm_memory(
                summary="摘要",
                context_matches=lambda: True,
                commit=commit,
            )
        self.assertEqual(cancelled.status, "cancelled")

        stale_dialog = SimpleNamespace(confirmed=True, exec=Mock())
        with patch(
            "ui.ai_result_coordinator.MemoryPreviewDialog",
            return_value=stale_dialog,
        ):
            with patch("ui.ai_result_coordinator.QMessageBox.warning"):
                stale = coordinator.confirm_memory(
                    summary="摘要",
                    context_matches=lambda: False,
                    commit=commit,
                )
        self.assertEqual(stale.status, "stale")
        commit.assert_not_called()

    def test_memory_confirmation_passes_counts_and_commits(self) -> None:
        commit = Mock(return_value="已写入")
        dialog = SimpleNamespace(confirmed=True, exec=Mock())
        with patch(
            "ui.ai_result_coordinator.MemoryPreviewDialog",
            return_value=dialog,
        ) as dialog_class:
            outcome = AIResultCoordinator().confirm_memory(
                summary="章节摘要",
                details="状态变更：\n- current.location: 山门 → 密林",
                patch_count=5,
                conflict_count=2,
                context_matches=lambda: True,
                commit=commit,
            )

        self.assertEqual(outcome.status, "committed")
        self.assertEqual(outcome.value, "已写入")
        commit.assert_called_once_with()
        dialog.exec.assert_called_once_with()
        dialog_class.assert_called_once_with(
            "章节摘要",
            "状态变更：\n- current.location: 山门 → 密林",
            5,
            2,
            None,
        )

    def test_repair_cancel_and_stale_context_do_not_apply(self) -> None:
        apply_replacement = Mock()
        dialog = SimpleNamespace(confirmed=False, exec=Mock())
        with patch("ui.ai_result_coordinator.RepairPreviewDialog", return_value=dialog):
            cancelled = AIResultCoordinator().confirm_repair(
                expected_original="原文",
                replacement="修复",
                explanation="说明",
                preserved_facts=(),
                context_matches=lambda: True,
                apply_replacement=apply_replacement,
            )
        self.assertEqual(cancelled.status, "cancelled")
        apply_replacement.assert_not_called()

        dialog = SimpleNamespace(confirmed=True, exec=Mock())
        with patch("ui.ai_result_coordinator.RepairPreviewDialog", return_value=dialog):
            with patch("ui.ai_result_coordinator.QMessageBox.warning"):
                stale = AIResultCoordinator().confirm_repair(
                    expected_original="原文",
                    replacement="修复",
                    explanation="说明",
                    preserved_facts=(),
                    context_matches=lambda: False,
                    apply_replacement=apply_replacement,
                )
        self.assertEqual(stale.status, "stale")
        apply_replacement.assert_not_called()
