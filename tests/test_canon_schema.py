import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path

from core.character_card_sync import MANAGED_STATE_START, MANAGED_STATE_END
from core.canon_schema import (
    SECTION_END, SECTION_START, CanonEntry, load_canon_entry, parse_canon_entry,
    render_canon_entry, schema_for, set_group_field, set_section_field, set_section_text,
    write_canon_entry,
)
from core.project_data import CANON_ENTRY_TYPES


def character_text() -> str:
    return (
        "# 白鸥\n\n"
        "<!-- novalist:character-card:v2 -->\n\n"
        "## 基础档案\n\n"
        "- 姓名：白鸥\n"
        "- 别名：林舟\n"
        "- 年龄／出生信息：不祥\n"
        "- 性别：女\n"
        "- 身份／阵营：旧站守灯人\n"
        "- 首次出场：chapter_01\n\n"
        "## 外貌与辨识特征\n\n"
        "左手腕有旧疤。\n\n"
        "## 性格、动机与核心欲望\n\n"
        "谨慎。\n\n"
        "## 长期目标与人物弧线\n\n"
        "找回记忆。\n\n"
        "## 能力档案\n\n"
        "### 潮汐感知\n\n"
        "- 所属体系：灵能体系\n"
        "- 能力属性：感知\n"
        "- 当前等级：一阶\n"
        "- 当前数值：—\n"
        "- 使用条件：临水\n"
        "- 限制与代价：消耗记忆片段\n"
        "- 克制关系：\n\n"
        "### 忆潮\n\n"
        "- 所属体系：灵能体系\n"
        "- 能力属性：记忆\n\n"
        "## 关键关系\n\n"
        "- 守塔老人：师徒\n\n"
        "## 秘密与人物弧线\n\n"
        "即是林舟。\n\n"
        "## 当前剧情状态（DeepSonder 同步）\n\n"
        f"{MANAGED_STATE_START}\n"
        "- 本次同步截止章节：chapter_02\n"
        "- 当前状态：健康\n"
        "- 当前位置：北港\n"
        "- 当前战力：未记录\n"
        "- 持有物：钥匙\n"
        "- 当前关系：无\n"
        f"{MANAGED_STATE_END}\n\n"
        "## 作者自由备注\n\n"
        "临时想法。\n"
    )


class CanonSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_templates_round_trip_byte_identical(self):
        for kind, spec in CANON_ENTRY_TYPES.items():
            text = str(spec["template"]).format(title="示例")
            entry = parse_canon_entry(kind, text)
            self.assertEqual(entry.title, "示例", kind)
            self.assertEqual(render_canon_entry(entry), text, kind)

    def test_filled_entry_round_trip_byte_identical(self):
        text = character_text()
        entry = parse_canon_entry("character", text)
        self.assertEqual(entry.title, "白鸥")
        self.assertEqual(entry.section("基础档案").fields["姓名"], "白鸥")
        self.assertEqual(entry.section("能力档案").groups[0].fields["当前等级"], "一阶")
        self.assertEqual(render_canon_entry(entry), text)

    def test_latin_colon_items_parse(self):
        entry = parse_canon_entry("character", "## 基础档案\n\n- 姓名: 白鸥\n")
        self.assertEqual(entry.section("基础档案").fields["姓名"], "白鸥")

    def test_unknown_kind_rejected(self):
        with self.assertRaisesRegex(ValueError, "未知的设定材料类型"):
            parse_canon_entry("faction", "# x\n")

    def test_field_edit_only_touches_its_section(self):
        text = character_text()
        entry = parse_canon_entry("character", text)
        next_heading = text.index("## 外貌与辨识特征")
        set_section_field(entry, "基础档案", "身份／阵营", "北港向导")
        rendered = render_canon_entry(entry)
        self.assertTrue(rendered.startswith(text[:next_heading].replace(
            "- 身份／阵营：旧站守灯人", "- 身份／阵营：北港向导")) or
            rendered.startswith(text[:text.index("## 基础档案")]))
        self.assertTrue(rendered.endswith(text[next_heading:]))
        self.assertIn(SECTION_START, rendered)
        marked = rendered[rendered.index(SECTION_START): rendered.index(SECTION_END) + len(SECTION_END)]
        self.assertIn("## 基础档案", marked)
        self.assertNotIn("## 外貌与辨识特征", marked)
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(reparsed.section("基础档案").fields["身份／阵营"], "北港向导")
        self.assertEqual(reparsed.section("基础档案").fields["别名"], "林舟")

    def test_handwritten_lines_survive_field_edit(self):
        text = character_text().replace(
            "- 首次出场：chapter_01\n",
            "- 首次出场：chapter_01\n左腕旧疤源于沉船事故（手写）。\n",
        )
        entry = parse_canon_entry("character", text)
        self.assertIn("左腕旧疤源于沉船事故（手写）。", entry.section("基础档案").extras[0])
        set_section_field(entry, "基础档案", "性别", "女")
        rendered = render_canon_entry(entry)
        self.assertIn("左腕旧疤源于沉船事故（手写）。", rendered)
        reparsed = parse_canon_entry("character", rendered)
        self.assertIn("左腕旧疤源于沉船事故（手写）。", reparsed.section("基础档案").extras)

    def test_unknown_heading_section_stays_verbatim(self):
        text = character_text() + "\n## 作者注（临时）\n\n草稿备注。\n"
        entry = parse_canon_entry("character", text)
        self.assertEqual(entry.section("作者注（临时）").role, "raw")
        set_section_field(entry, "基础档案", "性别", "女")
        rendered = render_canon_entry(entry)
        self.assertIn("## 作者注（临时）\n\n草稿备注。", rendered)

    def test_auto_state_block_bytes_preserved_across_edits(self):
        text = character_text()
        block = text[text.index(MANAGED_STATE_START): text.index(MANAGED_STATE_END) + len(MANAGED_STATE_END)]
        entry = parse_canon_entry("character", text)
        set_section_field(entry, "基础档案", "姓名", "白鸥")
        set_group_field(entry, "能力档案", "潮汐感知", "当前等级", "二阶")
        rendered = render_canon_entry(entry)
        self.assertIn(block, rendered)
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(reparsed.section("当前剧情状态（DeepSonder 同步）").role, "managed")

    def test_group_field_edit_and_reparse(self):
        entry = parse_canon_entry("character", character_text())
        set_group_field(entry, "能力档案", "潮汐感知", "当前等级", "二阶")
        set_group_field(entry, "能力档案", "忆潮", "能力属性", "记忆·检索")
        rendered = render_canon_entry(entry)
        marked = rendered[rendered.index(SECTION_START): rendered.index(SECTION_END) + len(SECTION_END)]
        self.assertIn("### 潮汐感知", marked)
        self.assertIn("### 忆潮", marked)
        reparsed = parse_canon_entry("character", rendered)
        groups = reparsed.section("能力档案").groups
        self.assertEqual([g.name for g in groups], ["潮汐感知", "忆潮"])
        self.assertEqual(groups[0].fields["当前等级"], "二阶")
        self.assertEqual(groups[1].fields["所属体系"], "灵能体系")

    def test_new_group_is_appended_and_reparsable(self):
        entry = parse_canon_entry("character", character_text())
        set_group_field(entry, "能力档案", "静默术", "所属体系", "灵能体系")
        reparsed = parse_canon_entry("character", render_canon_entry(entry))
        self.assertEqual([g.name for g in reparsed.section("能力档案").groups],
                         ["潮汐感知", "忆潮", "静默术"])

    def test_text_section_edit_and_reparse(self):
        entry = parse_canon_entry("character", character_text())
        set_section_text(entry, "外貌与辨识特征", "新的外貌描述。")
        reparsed = parse_canon_entry("character", render_canon_entry(entry))
        self.assertEqual(reparsed.section("外貌与辨识特征").text, "新的外貌描述。")

    def test_managed_and_free_sections_reject_form_edits(self):
        entry = parse_canon_entry("character", character_text())
        with self.assertRaisesRegex(ValueError, "只读"):
            set_section_field(entry, "当前剧情状态（DeepSonder 同步）", "当前状态", "昏迷")
        with self.assertRaisesRegex(ValueError, "只读"):
            set_section_text(entry, "当前剧情状态（DeepSonder 同步）", "x")
        with self.assertRaisesRegex(ValueError, "不是字段分区"):
            set_section_field(entry, "作者自由备注", "a", "b")
        self.assertEqual(render_canon_entry(entry), character_text())

    def test_edited_entry_renders_idempotently(self):
        entry = parse_canon_entry("character", character_text())
        set_section_field(entry, "基础档案", "性别", "女")
        first = render_canon_entry(entry)
        second = render_canon_entry(parse_canon_entry("character", first))
        self.assertEqual(first, second)
        self.assertEqual(parse_canon_entry("character", first).section("基础档案").marked, True)

    def test_stray_marker_lines_are_preserved(self):
        text = "# 白鸥\n\n" + SECTION_START + "\n\n## 基础档案\n\n- 姓名：白鸥\n"
        entry = parse_canon_entry("character", text)
        self.assertIn(SECTION_START, entry.preamble)
        self.assertEqual(render_canon_entry(entry), text)

    def test_missing_section_and_missing_field(self):
        entry = parse_canon_entry("character", "# 白鸥\n\n## 基础档案\n\n- 姓名：白鸥\n")
        self.assertIsNone(entry.section("作者自由备注"))
        with self.assertRaisesRegex(ValueError, "不存在"):
            set_section_field(entry, "外貌与辨识特征", "a", "b")

    def test_write_and_load_round_trip(self):
        path = Path(self.tmp.name) / "card.md"
        entry = parse_canon_entry("character", character_text())
        set_section_field(entry, "基础档案", "身份／阵营", "北港向导")
        write_canon_entry(path, entry)
        reloaded = load_canon_entry(path, "character")
        self.assertEqual(reloaded.section("基础档案").fields["身份／阵营"], "北港向导")
        self.assertEqual(reloaded.section("基础档案").fields["别名"], "林舟")
        self.assertIn("临时想法。", render_canon_entry(reloaded))


if __name__ == "__main__":
    unittest.main()
