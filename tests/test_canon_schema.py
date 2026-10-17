import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path

from core.character_card_sync import MANAGED_STATE_START, MANAGED_STATE_END
from core.canon_schema import (
    ROLE_RAW, SECTION_END, SECTION_START, CanonEntry, load_canon_entry,
    parse_canon_entry, raw_section_body, render_canon_entry, schema_for,
    set_group_extras, set_group_field, set_raw_section_text, set_section_extras,
    set_section_field, set_section_text, write_canon_entry,
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
        # 旧文件中的自定义小节按内容自动识别类型（此处为纯文本 → 文本型），
        # 未编辑时仍逐字节还原；无法无损结构化的内容回退 raw 的用例
        # 见 tests/test_canon_modules.py。
        text = character_text() + "\n## 作者注（临时）\n\n草稿备注。\n"
        entry = parse_canon_entry("character", text)
        self.assertEqual(entry.section("作者注（临时）").role, "text")
        self.assertEqual(entry.section("作者注（临时）").text, "草稿备注。")
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


class RawSectionAndExtrasTests(unittest.TestCase):
    """Phase C：原文区块（set_raw_section_text）与手写行（set_section_extras）的逐字写回。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    # ---------- set_raw_section_text ----------

    def test_set_raw_section_text_round_trip_and_idempotent(self):
        text = character_text()
        entry = parse_canon_entry("character", text)
        set_raw_section_text(entry, "作者自由备注", "新的想法一\n新的想法二")
        rendered = render_canon_entry(entry)
        # 其余区块逐字节不变（含受管同步区）
        self.assertTrue(rendered.startswith(text[:text.index("## 作者自由备注")]))
        self.assertIn("## 作者自由备注\n\n新的想法一\n新的想法二\n", rendered)
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(reparsed.section("作者自由备注").role, "free")
        self.assertEqual(raw_section_body(reparsed.section("作者自由备注")),
                         "新的想法一\n新的想法二")
        # 再次写入相同内容：幂等无操作，字节不变
        set_raw_section_text(reparsed, "作者自由备注", "新的想法一\n新的想法二")
        self.assertEqual(render_canon_entry(reparsed), rendered)

    def test_set_raw_section_text_no_change_preserves_bytes(self):
        text = character_text()
        entry = parse_canon_entry("character", text)
        # 与当前正文一致（仅行尾差异）：无操作，原字节（含结尾单个换行）保留
        set_raw_section_text(entry, "作者自由备注", "临时想法。\n")
        self.assertEqual(render_canon_entry(entry), text)

    def test_set_raw_section_text_crlf_section_keeps_crlf(self):
        text = character_text().replace(
            "## 作者自由备注\n\n临时想法。\n",
            "## 作者自由备注\r\n\r\n临时想法。\r\n")
        entry = parse_canon_entry("character", text)
        set_raw_section_text(entry, "作者自由备注", "窗口外有雾。")
        rendered = render_canon_entry(entry)
        self.assertIn("## 作者自由备注\r\n\r\n窗口外有雾。\r\n\r\n", rendered)
        reparsed = parse_canon_entry("character", rendered)
        set_raw_section_text(reparsed, "作者自由备注", "窗口外有雾。")
        self.assertEqual(render_canon_entry(reparsed), rendered)

    def test_set_raw_section_text_edits_raw_module(self):
        text = character_text() + "\n## 对照表\n\n| 键 | 值 |\n| --- | --- |\n| a | b |\n"
        entry = parse_canon_entry("character", text)
        self.assertEqual(entry.section("对照表").role, ROLE_RAW)
        set_raw_section_text(entry, "对照表", "| 键 | 值 |\n| --- | --- |\n| a | c |")
        rendered = render_canon_entry(entry)
        self.assertTrue(rendered.startswith(text[:text.index("## 对照表")]))
        self.assertIn("| a | c |", rendered)
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(reparsed.section("对照表").role, ROLE_RAW)
        self.assertEqual(raw_section_body(reparsed.section("对照表")),
                         "| 键 | 值 |\n| --- | --- |\n| a | c |")

    def test_set_raw_section_text_rejections(self):
        entry = parse_canon_entry("character", character_text())
        with self.assertRaisesRegex(ValueError, "不存在"):
            set_raw_section_text(entry, "没有这节", "x")
        with self.assertRaisesRegex(ValueError, "只读"):
            set_raw_section_text(entry, "当前剧情状态（DeepSonder 同步）", "x")
        with self.assertRaisesRegex(ValueError, "不是原文区块"):
            set_raw_section_text(entry, "外貌与辨识特征", "x")
        self.assertEqual(render_canon_entry(entry), character_text())

    # ---------- set_section_extras / set_group_extras ----------

    def test_set_section_extras_surgical_round_trip(self):
        text = character_text().replace(
            "- 首次出场：chapter_01\n",
            "- 首次出场：chapter_01\n手写行甲。\n手写行乙。\n")
        entry = parse_canon_entry("character", text)
        set_section_extras(entry, "基础档案", ["手写行甲（改）。", "手写行乙。"])
        rendered = render_canon_entry(entry)
        # extras-only 编辑不规范化字段部分，也不引入受管标记
        self.assertNotIn(SECTION_START, rendered)
        self.assertIn("- 首次出场：chapter_01\n手写行甲（改）。\n手写行乙。\n", rendered)
        self.assertIn("- 姓名：白鸥\n- 别名：林舟\n", rendered)
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(reparsed.section("基础档案").extras,
                         ["手写行甲（改）。", "手写行乙。"])
        self.assertEqual(reparsed.section("基础档案").fields["身份／阵营"], "旧站守灯人")
        # 幂等：相同内容再次写入不改变字节
        set_section_extras(reparsed, "基础档案", ["手写行甲（改）。", "手写行乙。"])
        self.assertEqual(render_canon_entry(reparsed), rendered)

    def test_set_section_extras_append_and_remove_lines(self):
        text = character_text().replace(
            "- 首次出场：chapter_01\n",
            "- 首次出场：chapter_01\n仅有一行手写。\n")
        entry = parse_canon_entry("character", text)
        set_section_extras(entry, "基础档案", ["仅有一行手写。", "新增手写行。"])
        rendered = render_canon_entry(entry)
        self.assertIn("- 首次出场：chapter_01\n仅有一行手写。\n新增手写行。\n", rendered)
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(reparsed.section("基础档案").extras,
                         ["仅有一行手写。", "新增手写行。"])
        # 清空：纯空白行被剔除，不产生解析漂移
        set_section_extras(reparsed, "基础档案", ["", "   "])
        self.assertEqual(reparsed.section("基础档案").extras, [])
        final = render_canon_entry(reparsed)
        self.assertNotIn("仅有一行手写。", final)
        self.assertEqual(parse_canon_entry("character", final)
                         .section("基础档案").extras, [])

    def test_set_section_extras_crlf_preserves_line_endings(self):
        text = character_text().replace("\n", "\r\n").replace(
            "- 首次出场：chapter_01\r\n",
            "- 首次出场：chapter_01\r\n手写行。\r\n")
        entry = parse_canon_entry("character", text)
        set_section_extras(entry, "基础档案", ["手写行（改）。"])
        rendered = render_canon_entry(entry)
        self.assertIn("- 首次出场：chapter_01\r\n手写行（改）。\r\n", rendered)

    def test_set_group_extras_round_trip(self):
        text = character_text().replace(
            "- 能力属性：记忆\n",
            "- 能力属性：记忆\n忆潮每晚自动触发。\n")
        entry = parse_canon_entry("character", text)
        self.assertEqual(
            next(g.extras for g in entry.section("能力档案").groups
                 if g.name == "忆潮"),
            ["忆潮每晚自动触发。"])
        set_group_extras(entry, "能力档案", "忆潮", ["触发条件：满月。"])
        rendered = render_canon_entry(entry)
        self.assertNotIn(SECTION_START, rendered)
        self.assertIn("- 能力属性：记忆\n触发条件：满月。\n", rendered)
        # 其他分组逐字节不受影响
        self.assertIn("- 使用条件：临水\n- 限制与代价：消耗记忆片段\n- 克制关系：\n",
                      rendered)
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(
            next(g.extras for g in reparsed.section("能力档案").groups
                 if g.name == "忆潮"),
            ["触发条件：满月。"])
        self.assertEqual(
            next(g.fields for g in reparsed.section("能力档案").groups
                 if g.name == "忆潮")["所属体系"], "灵能体系")

    def test_set_extras_on_dirty_section_keeps_content(self):
        entry = parse_canon_entry("character", character_text().replace(
            "- 首次出场：chapter_01\n",
            "- 首次出场：chapter_01\n手写行。\n"))
        set_section_field(entry, "基础档案", "性别", "女")  # 先置脏：走整块规范化渲染
        set_section_extras(entry, "基础档案", ["手写行（改）。"])
        rendered = render_canon_entry(entry)
        self.assertIn(SECTION_START, rendered)
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(reparsed.section("基础档案").extras, ["手写行（改）。"])
        self.assertEqual(reparsed.section("基础档案").fields["性别"], "女")
        self.assertEqual(reparsed.section("基础档案").fields["别名"], "林舟")

    def test_set_extras_rejections(self):
        entry = parse_canon_entry("character", character_text())
        with self.assertRaisesRegex(ValueError, "不存在"):
            set_section_extras(entry, "没有这节", ["x"])
        with self.assertRaisesRegex(ValueError, "不是字段或分组分区"):
            set_section_extras(entry, "外貌与辨识特征", ["x"])
        with self.assertRaisesRegex(ValueError, "不是字段或分组分区"):
            set_section_extras(entry, "当前剧情状态（DeepSonder 同步）", ["x"])
        with self.assertRaisesRegex(ValueError, "不是可重复分组分区"):
            set_group_extras(entry, "基础档案", "条目", ["x"])
        with self.assertRaisesRegex(ValueError, "不存在"):
            set_group_extras(entry, "能力档案", "没有条目", ["x"])
        self.assertEqual(render_canon_entry(entry), character_text())


if __name__ == "__main__":
    unittest.main()
