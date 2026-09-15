from unittest import TestCase

from core.text_chunking import chapter_content_hash, chunk_chapter


class ChapterChunkingTests(TestCase):
    def test_short_chapter_stays_in_one_chunk(self) -> None:
        text = "第一段。\n\n第二段。"
        chunks = chunk_chapter("chapter_01", text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].start_paragraph, 1)
        self.assertEqual(chunks[0].end_paragraph, 2)
        self.assertIn("第一段", chunks[0].text)
        self.assertIn("第二段", chunks[0].text)
        self.assertIn("[chapter_01:p0001]", chunks[0].annotated_text)
        self.assertIn("[chapter_01:p0002]", chunks[0].annotated_text)

    def test_long_chapter_is_bounded_and_covers_all_paragraphs(self) -> None:
        paragraphs = [f"P{index:02d}_" + "字" * 70 for index in range(1, 21)]
        chunks = chunk_chapter(
            "chapter_02",
            "\n\n".join(paragraphs),
            target_tokens=300,
            overlap_tokens=80,
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.estimated_tokens <= 300 for chunk in chunks))
        rendered = "\n".join(chunk.text for chunk in chunks)
        for index in range(1, 21):
            self.assertIn(f"P{index:02d}_", rendered)
        self.assertEqual([chunk.index for chunk in chunks], list(range(1, len(chunks) + 1)))

    def test_oversized_single_paragraph_is_split_on_safe_boundaries(self) -> None:
        text = "。".join("句子" + str(index) + "字" * 30 for index in range(50))
        chunks = chunk_chapter(
            "chapter_03",
            text,
            target_tokens=256,
            overlap_tokens=0,
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk.estimated_tokens <= 256 for chunk in chunks))
        self.assertTrue(all(chunk.start_paragraph == 1 for chunk in chunks))

    def test_normalized_content_hash_and_chunking_are_deterministic(self) -> None:
        lf = "甲。\n\n乙。"
        crlf = "甲。\r\n\r\n乙。"
        self.assertEqual(chapter_content_hash(lf), chapter_content_hash(crlf))
        first = chunk_chapter("chapter_04", lf)
        second = chunk_chapter("chapter_04", crlf)
        self.assertEqual(first, second)
