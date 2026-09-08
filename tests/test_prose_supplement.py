from unittest import TestCase

from core.ai_protocol import ExpansionInsertion
from core.prose_supplement import (
    apply_prose_insertions,
    build_prose_insertion_points,
)


class ProseSupplementTests(TestCase):
    def test_insertion_points_are_paragraph_boundaries_with_stable_ids(self) -> None:
        source = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
        points = build_prose_insertion_points(source)

        self.assertEqual([point.anchor_id for point in points], ["P001", "P002", "P003"])
        self.assertEqual(points[-1].offset, len(source))

        merged = apply_prose_insertions(
            source,
            (ExpansionInsertion("", "after", "新增细节。", "P002"),),
            insertion_points={point.anchor_id: point.offset for point in points},
        )
        self.assertEqual(
            merged,
            "第一段内容。\n\n第二段内容。\n\n新增细节。\n\n第三段内容。",
        )
