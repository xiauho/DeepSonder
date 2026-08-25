from unittest import TestCase

from ui.foreshadowing_selection_dialog import sort_foreshadowing_notes


class ForeshadowingSelectionTests(TestCase):
    def test_sort_prioritizes_high_and_near_term_notes(self) -> None:
        notes = [
            {
                "id": "low",
                "title": "低优先级",
                "priority": "low",
                "planned_resolution_chapter": "chapter_03",
                "status": "open",
            },
            {
                "id": "high",
                "title": "高优先级",
                "priority": "high",
                "planned_resolution_chapter": "chapter_08",
                "status": "open",
            },
            {
                "id": "resolved",
                "title": "已回收",
                "priority": "high",
                "planned_resolution_chapter": "chapter_02",
                "status": "resolved",
            },
        ]

        result = sort_foreshadowing_notes(notes, "chapter_02")

        self.assertEqual([note["id"] for note in result], ["high", "low"])

    def test_sort_does_not_mutate_input_notes(self) -> None:
        notes = [{"id": "f1", "title": "伏笔", "status": "open", "priority": "medium"}]
        result = sort_foreshadowing_notes(notes, "chapter_01")
        result[0]["title"] = "已修改副本"
        self.assertEqual(notes[0]["title"], "伏笔")
