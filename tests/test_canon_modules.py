"""Phase B：用户模块（非系统区块的新建/重命名/删除/移动）核心用例。

覆盖：旧文件小节的类型自动识别（字段/分组/文本 + raw 兜底）、未编辑模块
的逐字节 round-trip、add/rename/delete/move 的写盘往返与校验拒绝、
系统区块（基础档案与 DeepSonder 同步区）的结构保护、受管区块位置固定。
"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path

from core.canon_schema import (
    ROLE_FIELDS, ROLE_GROUP, ROLE_RAW, ROLE_TEXT,
    add_module, delete_module, detect_module_role, is_system_section,
    load_canon_entry, move_module, parse_canon_entry, rename_module,
    render_canon_entry, set_group_field, set_section_field, set_section_text,
    write_canon_entry,
)
from core.project_data import CANON_ENTRY_TYPES
from tests.test_canon_schema import character_text

MANAGED_HEADING = "当前剧情状态（DeepSonder 同步）"


def modules_text() -> str:
    """在标准角色卡末尾追加旧文件风格的自定义小节（未知标题）。"""
    return character_text() + (
        "\n## 出场物品\n\n"
        "### 长剑\n\n"
        "- 类型：武器\n"
        "- 状态：完好\n\n"
        "## 时间线\n\n"
        "- 第一章：北港\n"
        "- 第二章：南屿\n\n"
        "## 作者注\n\n"
        "纯文本备注。\n\n"
        "## 对照表\n\n"
        "| 键 | 值 |\n"
        "| --- | --- |\n"
        "| a | b |\n"
    )


class ModuleDetectionTests(unittest.TestCase):
    def test_detect_role_rules(self):
        self.assertEqual(detect_module_role("分组", "### 长剑\n\n- 类型：武器\n"), ROLE_GROUP)
        self.assertEqual(detect_module_role("字段", "- 第一章：北港\n- 第二章：南屿\n"), ROLE_FIELDS)
        self.assertEqual(detect_module_role("文本", "纯文本备注。\n"), ROLE_TEXT)
        self.assertEqual(detect_module_role("空白", ""), ROLE_TEXT)
        self.assertEqual(detect_module_role("骨架", "### 子分组\n\n散文。\n"), ROLE_TEXT)
        self.assertEqual(detect_module_role("表格", "| 键 | 值 |\n| --- | --- |\n"), ROLE_RAW)
        self.assertEqual(detect_module_role("代码", "```python\nx = 1\n```\n"), ROLE_RAW)
        self.assertEqual(detect_module_role("标记", "<!-- novalist:x -->\n正文\n"), ROLE_RAW)
        # 顶层字段与子分组混排无法按单一角色无损渲染，整体保持原样
        self.assertEqual(detect_module_role("混排", "- a：b\n\n### 子\n\n- c：d\n"), ROLE_RAW)

    def test_auto_modules_parsed_with_roles(self):
        entry = parse_canon_entry("character", modules_text())
        items = entry.section("出场物品")
        self.assertEqual(items.role, ROLE_GROUP)
        self.assertEqual(items.groups[0].fields["类型"], "武器")
        timeline = entry.section("时间线")
        self.assertEqual(timeline.role, ROLE_FIELDS)
        self.assertEqual(timeline.fields["第一章"], "北港")
        note = entry.section("作者注")
        self.assertEqual(note.role, ROLE_TEXT)
        self.assertEqual(note.text, "纯文本备注。")
        table = entry.section("对照表")
        self.assertEqual(table.role, ROLE_RAW)

    def test_never_edited_auto_modules_round_trip_verbatim(self):
        text = modules_text()
        entry = parse_canon_entry("character", text)
        self.assertEqual(render_canon_entry(entry), text)

    def test_edited_auto_module_leaves_siblings_verbatim(self):
        text = modules_text()
        entry = parse_canon_entry("character", text)
        set_section_text(entry, "作者注", "新的备注。")
        rendered = render_canon_entry(entry)
        # 未编辑的自动识别模块（分组/字段/raw）逐字节保留
        self.assertIn("## 出场物品\n\n### 长剑\n\n- 类型：武器\n- 状态：完好\n", rendered)
        self.assertIn("## 时间线\n\n- 第一章：北港\n- 第二章：南屿\n", rendered)
        self.assertIn("## 对照表\n\n| 键 | 值 |\n| --- | --- |\n| a | b |", rendered)
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(reparsed.section("作者注").text, "新的备注。")


class ModuleMutationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "card.md"

    def test_is_system_section(self):
        entry = parse_canon_entry("character", character_text())
        self.assertTrue(is_system_section(entry, entry.section("基础档案")))
        self.assertTrue(is_system_section(entry, entry.section(MANAGED_HEADING)))
        self.assertFalse(is_system_section(entry, entry.section("外貌与辨识特征")))
        self.assertFalse(is_system_section(entry, entry.section("作者自由备注")))
        world_text = str(CANON_ENTRY_TYPES["world"]["template"]).format(title="北港")
        world_entry = parse_canon_entry("world", world_text)
        # world/power 无 fixed 结构：全部声明区块都是普通用户模块
        self.assertFalse(is_system_section(world_entry, world_entry.section("核心规则")))

    def test_add_module_inserts_before_managed_and_reparses(self):
        entry = parse_canon_entry("character", character_text())
        section = add_module(entry, "旅途见闻", ROLE_TEXT)
        self.assertTrue(section.dirty)
        headings = [s.heading for s in entry.sections]
        self.assertLess(headings.index("旅途见闻"), headings.index(MANAGED_HEADING))
        rendered = render_canon_entry(entry)
        self.assertIn("临时想法。", rendered)  # 受管区之后的区块不受影响
        reparsed = parse_canon_entry("character", rendered)
        self.assertIsNotNone(reparsed.section("旅途见闻"))
        self.assertEqual(reparsed.section(MANAGED_HEADING).role, "managed")

    def test_add_module_world_appends_at_end(self):
        text = str(CANON_ENTRY_TYPES["world"]["template"]).format(title="北港")
        entry = parse_canon_entry("world", text)
        add_module(entry, "货币与物价", ROLE_FIELDS)
        self.assertEqual([s.heading for s in entry.sections][-1], "货币与物价")

    def test_add_module_validations(self):
        entry = parse_canon_entry("character", character_text())
        with self.assertRaisesRegex(ValueError, "不能为空"):
            add_module(entry, "  ", ROLE_TEXT)
        with self.assertRaisesRegex(ValueError, "已存在"):
            add_module(entry, "基础档案", ROLE_TEXT)  # 系统区块标题同样占用
        with self.assertRaisesRegex(ValueError, "已存在"):
            add_module(entry, MANAGED_HEADING, ROLE_TEXT)  # 受管区块标题同样占用
        with self.assertRaisesRegex(ValueError, "类型"):
            add_module(entry, "新模块", "raw")
        with self.assertRaisesRegex(ValueError, "换行"):
            add_module(entry, "带\n换行", ROLE_TEXT)
        add_module(entry, "Notes", ROLE_TEXT)
        with self.assertRaisesRegex(ValueError, "已存在"):
            add_module(entry, "notes", ROLE_TEXT)  # casefold 去重
        self.assertEqual(
            [s.heading for s in entry.sections].count("Notes"), 1)

    def test_add_module_write_reparse_fields_and_group(self):
        entry = parse_canon_entry("character", character_text())
        add_module(entry, "弱点清单", ROLE_FIELDS)
        set_section_field(entry, "弱点清单", "当前弱点", "怕水")
        add_module(entry, "随身物品", ROLE_GROUP)
        set_group_field(entry, "随身物品", "钥匙", "说明", "开北港大门")
        write_canon_entry(self.path, entry)
        reloaded = load_canon_entry(self.path, "character")
        weak = reloaded.section("弱点清单")
        self.assertEqual(weak.role, ROLE_FIELDS)
        self.assertEqual(weak.fields["当前弱点"], "怕水")
        items = reloaded.section("随身物品")
        self.assertEqual(items.role, ROLE_GROUP)
        self.assertEqual(items.groups[0].fields["说明"], "开北港大门")

    def test_rename_preserves_body_bytes_exactly(self):
        text = modules_text()
        entry = parse_canon_entry("character", text)
        rename_module(entry, "作者注", "创作札记")
        rendered = render_canon_entry(entry)
        self.assertEqual(rendered, text.replace("## 作者注", "## 创作札记"))
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(reparsed.section("创作札记").text, "纯文本备注。")
        self.assertIsNone(reparsed.section("作者注"))

    def test_rename_raw_module_preserves_bytes(self):
        text = modules_text()
        entry = parse_canon_entry("character", text)
        rename_module(entry, "对照表", "旧版对照")
        rendered = render_canon_entry(entry)
        self.assertEqual(rendered, text.replace("## 对照表", "## 旧版对照"))
        self.assertEqual(parse_canon_entry("character", rendered).section("旧版对照").role, ROLE_RAW)

    def test_rename_group_section_becomes_detected_group(self):
        entry = parse_canon_entry("character", character_text())
        rename_module(entry, "能力档案", "武艺档案")
        reparsed = parse_canon_entry("character", render_canon_entry(entry))
        renamed = reparsed.section("武艺档案")
        self.assertEqual(renamed.role, ROLE_GROUP)
        self.assertEqual([g.name for g in renamed.groups], ["潮汐感知", "忆潮"])

    def test_rename_rejections(self):
        entry = parse_canon_entry("character", character_text())
        with self.assertRaisesRegex(ValueError, "系统区块"):
            rename_module(entry, "基础档案", "档案")
        with self.assertRaisesRegex(ValueError, "系统区块"):
            rename_module(entry, MANAGED_HEADING, "状态")
        with self.assertRaisesRegex(ValueError, "不存在"):
            rename_module(entry, "没有这节", "x")
        with self.assertRaisesRegex(ValueError, "已存在"):
            rename_module(entry, "外貌与辨识特征", "基础档案")
        with self.assertRaisesRegex(ValueError, "不能为空"):
            rename_module(entry, "外貌与辨识特征", "  ")
        self.assertEqual(render_canon_entry(entry), character_text())

    def test_delete_removes_span_and_keeps_others_byte_identical(self):
        text = modules_text()
        entry = parse_canon_entry("character", text)
        span = "## 时间线\n\n- 第一章：北港\n- 第二章：南屿\n\n"
        self.assertIn(span, text)
        delete_module(entry, "时间线")
        rendered = render_canon_entry(entry)
        self.assertEqual(rendered, text.replace(span, ""))
        reparsed = parse_canon_entry("character", rendered)
        self.assertIsNone(reparsed.section("时间线"))
        self.assertEqual(reparsed.section("出场物品").groups[0].fields["类型"], "武器")
        self.assertIn("纯文本备注。", rendered)

    def test_delete_rejections(self):
        entry = parse_canon_entry("character", character_text())
        with self.assertRaisesRegex(ValueError, "系统区块"):
            delete_module(entry, "基础档案")
        with self.assertRaisesRegex(ValueError, "系统区块"):
            delete_module(entry, MANAGED_HEADING)
        with self.assertRaisesRegex(ValueError, "不存在"):
            delete_module(entry, "没有这节")
        self.assertEqual(render_canon_entry(entry), character_text())

    def test_move_swaps_neighbors_and_keeps_systems_fixed(self):
        text = character_text()
        entry = parse_canon_entry("character", text)
        managed_index = [s.heading for s in entry.sections].index(MANAGED_HEADING)
        move_module(entry, "外貌与辨识特征", 1)  # 下移：与“性格”交换
        headings = [s.heading for s in entry.sections]
        self.assertEqual(headings[1], "性格、动机与核心欲望")
        self.assertEqual(headings[2], "外貌与辨识特征")
        self.assertEqual(headings.index(MANAGED_HEADING), managed_index)
        self.assertEqual(headings.index("基础档案"), 0)
        # 纯重排：各区块 raw 逐字节保留，仅顺序变化，不引入受管标记
        rendered = render_canon_entry(entry)
        self.assertNotIn("novalist:section:v1", rendered)
        self.assertIn("## 性格、动机与核心欲望\n\n谨慎。\n\n## 外貌与辨识特征\n\n左手腕有旧疤。\n\n",
                      rendered)
        reparsed = parse_canon_entry("character", rendered)
        self.assertEqual(reparsed.section("外貌与辨识特征").text, "左手腕有旧疤。")

    def test_move_skips_system_blocks(self):
        entry = parse_canon_entry("character", character_text())

        def managed_between_neighbours(headings):
            # 受管区不被穿越：始终位于“关键关系”之后、“作者自由备注”之前
            return (headings.index("关键关系") < headings.index(MANAGED_HEADING)
                    < headings.index("作者自由备注"))

        headings = [s.heading for s in entry.sections]
        self.assertTrue(managed_between_neighbours(headings))
        # 秘密与人物弧线 下移：越过受管区与末尾的“作者自由备注”交换
        move_module(entry, "秘密与人物弧线", 1)
        headings = [s.heading for s in entry.sections]
        self.assertEqual(headings[-2:], ["作者自由备注", "秘密与人物弧线"])
        self.assertTrue(managed_between_neighbours(headings))
        # 末尾的“秘密与人物弧线” 上移：与“作者自由备注”直接交换回来
        move_module(entry, "秘密与人物弧线", -1)
        headings = [s.heading for s in entry.sections]
        self.assertEqual(headings[-2:], ["秘密与人物弧线", "作者自由备注"])
        self.assertTrue(managed_between_neighbours(headings))

    def test_move_rejections_and_boundaries(self):
        entry = parse_canon_entry("character", character_text())
        with self.assertRaisesRegex(ValueError, "受管区块"):
            move_module(entry, MANAGED_HEADING, -1)
        with self.assertRaisesRegex(ValueError, "系统区块"):
            move_module(entry, "基础档案", 1)
        with self.assertRaisesRegex(ValueError, "不存在"):
            move_module(entry, "没有这节", 1)
        with self.assertRaisesRegex(ValueError, "边界"):
            move_module(entry, "外貌与辨识特征", -1)  # 上面是系统区块基础档案
        with self.assertRaisesRegex(ValueError, "边界"):
            move_module(entry, "作者自由备注", 1)  # 已是最后一个
        self.assertEqual(render_canon_entry(entry), character_text())

    def test_module_ops_write_and_reload_full_cycle(self):
        self.path.write_text(character_text(), encoding="utf-8")
        entry = load_canon_entry(self.path, "character")
        add_module(entry, "旅途见闻", ROLE_TEXT)
        rename_module(entry, "旅途见闻", "行脚记录")
        move_module(entry, "行脚记录", -1)
        delete_module(entry, "长期目标与人物弧线")
        write_canon_entry(self.path, entry)
        reloaded = load_canon_entry(self.path, "character")
        headings = [s.heading for s in reloaded.sections]
        self.assertIn("行脚记录", headings)
        self.assertNotIn("长期目标与人物弧线", headings)
        self.assertEqual(reloaded.section(MANAGED_HEADING).role, "managed")
        # 其余区块内容保持原样
        self.assertEqual(reloaded.section("基础档案").fields["姓名"], "白鸥")
        self.assertEqual(reloaded.section("外貌与辨识特征").text, "左手腕有旧疤。")
        self.assertIn("临时想法。", render_canon_entry(reloaded))


if __name__ == "__main__":
    unittest.main()
