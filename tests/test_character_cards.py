import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.character_cards import (
    create_character_cards,
    missing_character_cards,
    render_character_card,
)
from core.project import NovelProject


class CharacterCardSyncTests(unittest.TestCase):
    def test_missing_cards_only_returns_unmatched_tracked_characters(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            (project.canon_dir / "characters" / "林夜.md").write_text(
                "# 林夜\n", encoding="utf-8"
            )
            candidates = missing_character_cards(
                project,
                {
                    "characters": {
                        "林夜": {"state": "已有卡"},
                        "崔岩": {"state": "率队突围"},
                        "魔将": {"location": "断魂谷"},
                    }
                },
            )

            self.assertEqual([item.name for item in candidates], ["崔岩", "魔将"])

    def test_create_cards_renders_state_and_never_overwrites(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            candidates = missing_character_cards(
                project,
                {"characters": {"崔岩": {"state": "率队突围", "items": ["长刀"]}}},
            )
            created = create_character_cards(project, candidates)
            self.assertEqual([path.stem for path in created], ["崔岩"])
            self.assertIn("率队突围", created[0].read_text(encoding="utf-8"))
            self.assertIn("长刀", created[0].read_text(encoding="utf-8"))

            candidates_again = missing_character_cards(
                project,
                {"characters": {"崔岩": {"state": "新状态"}}},
            )
            self.assertEqual(candidates_again, [])
            self.assertIn("率队突围", created[0].read_text(encoding="utf-8"))

    def test_empty_details_still_produce_a_editable_draft(self) -> None:
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            candidate = missing_character_cards(
                project, {"characters": {"神秘人": {}}}
            )[0]
            self.assertIn("待完善角色卡", render_character_card(candidate))


if __name__ == "__main__":
    unittest.main()
