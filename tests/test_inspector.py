from unittest import TestCase

from ui.inspector import rank_character_names


class InspectorTests(TestCase):
    def test_characters_are_ranked_by_current_chapter_frequency(self) -> None:
        names = ["伊岚", "周弦", "顾衍"]
        chapter = "周弦看向伊岚。周弦又叫了伊岚一声。周弦没有等顾衍。"

        self.assertEqual(rank_character_names(names, chapter), ["周弦", "伊岚", "顾衍"])

    def test_equal_frequency_keeps_existing_order(self) -> None:
        names = ["伊岚", "周弦", "顾衍"]
        chapter = "伊岚和周弦走过顾衍留下的门。"

        self.assertEqual(rank_character_names(names, chapter), names)
