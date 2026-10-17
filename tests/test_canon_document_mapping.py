"""Round-trip and identity guarantees for the modular Markdown document model."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.canon_schema import (
    SECTION_START, SECTION_END, add_module, delete_module, load_canon_entry,
    move_module, parse_canon_entry, raw_section_body, render_canon_entry,
    rename_module, set_group_field, set_raw_section_text, set_section_field,
    set_section_text, write_canon_entry,
)
from tests.test_canon_schema import character_text


class CanonDocumentMappingTests(unittest.TestCase):
    def test_empty_custom_types_and_identity_survive_reopen(self):
        for role in ("fields", "group", "text"):
            with self.subTest(role=role):
                entry = parse_canon_entry("world", "# 世界\n\n")
                section = add_module(entry, "空模块", role)
                first = render_canon_entry(entry)
                loaded = parse_canon_entry("world", first)
                self.assertEqual(loaded.section("空模块").role, role)
                self.assertEqual(loaded.section("空模块").module_id, section.module_id)
                self.assertEqual(render_canon_entry(loaded), first)

    def test_renamed_schema_module_keeps_schema_and_identity(self):
        entry = parse_canon_entry("character", character_text())
        section = entry.section("能力档案")
        identity = section.module_id
        rename_module(entry, identity, "修炼档案")
        saved = render_canon_entry(entry)
        loaded = parse_canon_entry("character", saved).section("修炼档案")
        self.assertEqual(loaded.module_id, identity)
        self.assertEqual(loaded.schema_key, "能力档案")
        self.assertEqual(loaded.role, "group")
        self.assertNotIn("deepsonder:module", raw_section_body(loaded))

    def test_duplicate_fields_and_groups_fall_back_without_losing_lines(self):
        examples = [
            ("## 基础档案\n\n- 姓名：甲\n- 姓名：乙\n", "基础档案"),
            ("## 能力档案\n\n### 同名\n- 类型：甲\n### 同名\n- 类型：乙\n", "能力档案"),
            ("## 能力档案\n\n- 顶层：不能丢\n### 能力\n- 类型：甲\n", "能力档案"),
            ("## 基础档案\n\n### 手写子节\n- 姓名：乙\n", "基础档案"),
        ]
        for source, heading in examples:
            with self.subTest(heading=heading, source=source):
                entry = parse_canon_entry("character", source)
                self.assertEqual(entry.section(heading).role, "raw")
                self.assertEqual(render_canon_entry(entry), source)
                original_body = raw_section_body(entry.section(heading))
                set_raw_section_text(entry, heading, original_body + "\n补充")
                saved = render_canon_entry(entry)
                self.assertIn(original_body, saved)
                self.assertIn("补充", saved)

    def test_duplicate_headings_are_independently_addressable(self):
        source = "# T\n\n## 同名\n\n第一段\n\n## 同名\n\n第二段\n"
        entry = parse_canon_entry("world", source)
        self.assertEqual(len({s.module_id for s in entry.sections}), 2)
        second = entry.sections[1]
        if second.role == "raw":
            set_raw_section_text(entry, second.module_id, "第二段改动")
        else:
            set_section_text(entry, second.module_id, "第二段改动")
        saved = render_canon_entry(entry)
        self.assertTrue(saved.startswith("# T\n\n## 同名\n\n第一段\n\n"))
        self.assertIn("第二段改动", saved)
        with self.assertRaisesRegex(ValueError, "同名|不唯一"):
            delete_module(entry, "同名")
        rename_module(entry, second.module_id, "新名称")
        self.assertEqual(entry.sections[0].heading, "同名")
        self.assertEqual(entry.sections[1].heading, "新名称")

    def test_code_fences_and_comments_do_not_create_fake_modules(self):
        for opener, closer in [("```md", "```"), ("~~~~text", "~~~~")]:
            source = f"# T\n\n## 代码\n\n{opener}\n## 假标题\n{SECTION_START}\n{SECTION_END}\n{closer}\n\n<!--\n## 注释标题\n-->\n\n## 真模块\n\n正文\n"
            entry = parse_canon_entry("world", source)
            self.assertEqual([s.heading for s in entry.sections], ["代码", "真模块"])
            self.assertEqual(render_canon_entry(entry), source)
            set_section_text(entry, "真模块", "新正文")
            self.assertIn(source[source.index("## 代码"):source.index("## 真模块")], render_canon_entry(entry))

    def test_frontmatter_and_fenced_preamble_keep_headers_opaque(self):
        source = "---\nlabel: |\n  ## 配置标题\n---\n# T\n\n```md\n## 示例标题\n```\n\n## 真模块\n正文\n"
        entry = parse_canon_entry("world", source)
        self.assertEqual([s.heading for s in entry.sections], ["真模块"])
        self.assertEqual(entry.title, "T")
        self.assertEqual(render_canon_entry(entry), source)

    def test_marked_suffix_survives_edit_and_delete(self):
        source = f"# T\n\n{SECTION_START}\n## 核心规则\n\n旧规则\n{SECTION_END}\n\n区块外手写内容\n\n## 历史与现状\n\n历史\n"
        entry = parse_canon_entry("world", source)
        set_section_text(entry, "核心规则", "新规则")
        saved = render_canon_entry(entry)
        self.assertIn("区块外手写内容\n\n## 历史与现状", saved)
        delete_module(entry, "核心规则")
        self.assertIn("区块外手写内容", render_canon_entry(entry))
        source = character_text().split("## 作者自由备注")[0]
        source += SECTION_START + "\n## 附加模块\n\n附加文字\n" + SECTION_END + "\n\n保留备注\n"
        entry = parse_canon_entry("character", source)
        delete_module(entry, "附加模块")
        saved = render_canon_entry(entry)
        loaded = parse_canon_entry("character", saved)
        managed = loaded.section("当前剧情状态（DeepSonder 同步）")
        self.assertEqual(managed.role, "managed")
        self.assertFalse(managed.opaque)
        self.assertIn("保留备注", managed.suffix)
        self.assertNotIn("deepsonder:module", saved)

    def test_moving_module_leaves_between_module_notes_in_place(self):
        source = f"# T\n\n{SECTION_START}\n## A\n\n甲\n{SECTION_END}\n\n间隙备注\n\n## B\n\n乙\n"
        entry = parse_canon_entry("world", source)
        move_module(entry, "A", 1)
        saved = render_canon_entry(entry)
        self.assertLess(saved.index("## B"), saved.index("间隙备注"))
        self.assertLess(saved.index("间隙备注"), saved.index("## A"))

    def test_edit_keeps_other_spans_bytes_including_mixed_newlines(self):
        source = "\ufeff# T\r\n\r\n## 核心规则\r\n\r\n旧规则\r\n\r\n## 历史与现状\n\n手写历史\n"
        entry = parse_canon_entry("world", source)
        set_section_text(entry, "核心规则", "新规则")
        saved = render_canon_entry(entry)
        self.assertTrue(saved.startswith("\ufeff# T\r\n\r\n"))
        self.assertTrue(saved.endswith("## 历史与现状\n\n手写历史\n"))
        changed = saved[:saved.index("## 历史与现状")]
        self.assertNotIn("\n", changed.replace("\r\n", ""))
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "entry.md"
            path.write_bytes(source.encode("utf-8"))
            loaded = load_canon_entry(path, "world")
            self.assertEqual(render_canon_entry(loaded).encode("utf-8"), source.encode("utf-8"))
            set_section_text(loaded, "核心规则", "新规则")
            write_canon_entry(path, loaded)
            self.assertTrue(path.read_bytes().endswith(b"## " + "历史与现状\n\n手写历史\n".encode("utf-8")))

    def test_appending_module_to_no_newline_document_is_parseable(self):
        entry = parse_canon_entry("world", "# T\n\n## 原模块\n\n末尾无换行")
        add_module(entry, "新模块", "text")
        loaded = parse_canon_entry("world", render_canon_entry(entry))
        self.assertEqual([s.heading for s in loaded.sections], ["原模块", "新模块"])

    def test_text_and_raw_edits_keep_nested_headings_inside_module(self):
        entry = parse_canon_entry("world", "# T\n\n## 文本\n\n原文\n\n## 表格\n\n| a | b |\n")
        set_section_text(entry, "文本", "正文\n## 内部标题\n文本")
        set_raw_section_text(entry, "表格", "| a | c |\n## 原文内部标题\n末尾")
        loaded = parse_canon_entry("world", render_canon_entry(entry))
        self.assertEqual([s.heading for s in loaded.sections], ["文本", "表格"])
        self.assertIn("## 原文内部标题", raw_section_body(loaded.section("表格")))

    def test_unknown_metadata_is_preserved_as_opaque_source(self):
        source = '# T\n\n<!-- deepsonder:module:v2 {"future":true} -->\n## 新协议模块\n\n原文\n\n## 核心规则\n规则\n'
        entry = parse_canon_entry("world", source)
        opaque = entry.sections[0]
        self.assertEqual(opaque.role, "raw")
        self.assertIn("deepsonder:module:v2", raw_section_body(opaque))
        set_section_text(entry, "核心规则", "新规则")
        self.assertIn(opaque.raw, render_canon_entry(entry))
        no_heading = SECTION_START + "\n只有正文\n" + SECTION_END
        broken = parse_canon_entry("world", no_heading)
        with self.assertRaisesRegex(ValueError, "标题"):
            rename_module(broken, broken.sections[0].module_id, "假重命名")
        self.assertEqual(render_canon_entry(broken), no_heading)

    def test_invalid_metadata_types_do_not_crash_or_unlock_managed_state(self):
        source = '<!-- deepsonder:module:v1 {"id":"' + 'a'*32 + '","role":[],"schema":""} -->\n## 数据\n\n原文\n'
        self.assertEqual(parse_canon_entry("world", source).sections[0].role, "raw")
        entry = parse_canon_entry("character", character_text())
        section = add_module(entry, "新文本", "text")
        saved = render_canon_entry(entry)
        meta = next(line for line in saved.splitlines() if line.startswith("<!-- deepsonder:module:"))
        saved = saved.replace("## 当前剧情状态（DeepSonder 同步）", meta + "\n## 当前剧情状态（DeepSonder 同步）")
        loaded = parse_canon_entry("character", saved)
        managed = loaded.section("当前剧情状态（DeepSonder 同步）")
        self.assertEqual(managed.role, "managed")
        with self.assertRaisesRegex(ValueError, "只读"):
            set_raw_section_text(loaded, managed.module_id, "覆盖")

    def test_duplicate_ids_are_isolated_and_repaired_only_when_edited(self):
        entry = parse_canon_entry("world", "# T\n")
        first = add_module(entry, "A", "text")
        second = add_module(entry, "B", "text")
        source = render_canon_entry(entry).replace(second.module_id, first.module_id)
        loaded = parse_canon_entry("world", source)
        self.assertEqual(render_canon_entry(loaded), source)
        self.assertEqual(len({s.module_id for s in loaded.sections}), 2)
        self.assertTrue(all(s.role == "raw" for s in loaded.sections))
        set_raw_section_text(loaded, loaded.sections[1].module_id, "B的正文")
        repaired = parse_canon_entry("world", render_canon_entry(loaded))
        self.assertEqual(len({s.module_id for s in repaired.sections}), 2)

    def test_incomplete_new_syntax_is_rejected_without_touching_source(self):
        for body in ("```md\n未闭合", "~~~\n未闭合", "<!-- 完整 --> <!-- 未闭合", "<!--\n--> <!-- 未闭合", "<pre>\n未闭合", SECTION_END):
            with self.subTest(body=body):
                entry = parse_canon_entry("world", "# T\n\n## 文本\n\n原文\n")
                original = render_canon_entry(entry)
                with self.assertRaises(ValueError):
                    set_section_text(entry, "文本", body)
                self.assertEqual(render_canon_entry(entry), original)
        entry = parse_canon_entry("world", "# T\n\n## 文本\n\n原文\n")
        set_section_text(entry, "文本", "---\n合法水平线")
        self.assertIn("合法水平线", render_canon_entry(entry))

    def test_gap_still_has_its_own_span_after_move_and_reload(self):
        source = f"# T\n\n{SECTION_START}\n## A\n\n甲\n{SECTION_END}\n\n独立间隙备注\n\n## B\n\n乙\n"
        entry = parse_canon_entry("world", source)
        move_module(entry, "A", 1)
        loaded = parse_canon_entry("world", render_canon_entry(entry))
        self.assertIn("独立间隙备注", loaded.sections[0].suffix)
        self.assertNotIn("独立间隙备注", loaded.sections[0].text)

    def test_context_ignores_real_metadata_but_retains_fenced_examples(self):
        from core.canon_schema import visible_canon_text
        from core.project import NovelProject
        entry = parse_canon_entry("world", "# 港口\n\n## 规则\n\n港口开放\n")
        rename_module(entry, "规则", "通行规则")
        source = render_canon_entry(entry)
        meta = next(line for line in source.splitlines() if line.startswith("<!-- deepsonder:module:"))
        self.assertNotIn(meta, visible_canon_text(source))
        example = "```md\n" + meta + "\n```\n"
        self.assertEqual(visible_canon_text(example), example)
        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "project", "上下文测试")
            path = project.canon_dir / "world" / "港口.md"
            path.write_bytes(source.encode("utf-8"))
            related = project.find_related_canon("chapter_01", relevance_query="港口", include_power=False)
            self.assertIn("港口开放", related.world)
            self.assertNotIn("deepsonder:module", related.world)

    def test_relationship_graph_uses_semantic_section_after_rename(self):
        from application.relationship_graph_service import RelationshipGraphService
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "白鸥.md"
            entry = parse_canon_entry("character", character_text())
            rename_module(entry, "关键关系", "往来人物")
            path.write_bytes(render_canon_entry(entry).encode("utf-8"))
            card = RelationshipGraphService._read_card(path)
            self.assertIn(("守塔老人", "师徒"), card.relations)

    def test_character_sync_keeps_metadata_and_outside_bytes(self):
        from core.character_card_sync import replace_managed_state, parse_managed_state, MANAGED_STATE_START, MANAGED_STATE_END
        source = character_text().replace("\n", "\r\n")
        entry = parse_canon_entry("character", source)
        rename_module(entry, "关键关系", "往来人物")
        source = render_canon_entry(entry)
        saved = replace_managed_state(source, {"as_of_chapter":"chapter_03", "state":"已恢复"})
        self.assertEqual(source[:source.index(MANAGED_STATE_START)], saved[:saved.index(MANAGED_STATE_START)])
        self.assertEqual(source[source.index(MANAGED_STATE_END)+len(MANAGED_STATE_END):], saved[saved.index(MANAGED_STATE_END)+len(MANAGED_STATE_END):])
        self.assertNotIn("\n", saved.replace("\r\n", ""))
        self.assertEqual(parse_managed_state(saved)["state"], "已恢复")
        self.assertEqual(parse_canon_entry("character", saved).section("往来人物").schema_key, "关键关系")


    def test_opaque_source_and_outside_note_have_separate_edit_spans(self):
        from core.canon_schema import set_section_suffix
        source = (SECTION_START + '\n<!-- deepsonder:module:v2 {} -->\n'
                  '## 未知格式\n\n原文\n' + SECTION_END + '\n\n区块外备注\n')
        entry = parse_canon_entry("world", source)
        section = entry.sections[0]
        body = raw_section_body(section)
        self.assertNotIn("区块外备注", body)
        set_raw_section_text(entry, section.module_id, body.replace("原文", "新原文"))
        set_section_suffix(entry, section.module_id, "新备注")
        saved = render_canon_entry(entry)
        self.assertEqual(saved.count("新备注"), 1)
        self.assertIn("新原文", saved)
        self.assertIn(SECTION_END + "\n\n新备注\n", saved)

    def test_handwritten_edits_cannot_insert_document_control_syntax(self):
        from core.canon_schema import set_section_extras, set_group_extras
        for invalid in (SECTION_END, "## 新模块", "<!-- 未结束"):
            for group in (False, True):
                with self.subTest(invalid=invalid, group=group):
                    entry = parse_canon_entry("character", character_text())
                    original = render_canon_entry(entry)
                    with self.assertRaisesRegex(ValueError, "正文|导语|资料标记"):
                        if group:
                            set_group_extras(entry, "能力档案", "潮汐感知", [invalid])
                        else:
                            set_section_extras(entry, "基础档案", [invalid])
                    self.assertEqual(render_canon_entry(entry), original)

    def test_status_uses_original_schema_after_module_rename(self):
        from core.canon_schema import entry_status
        entry = parse_canon_entry("world", "# T\n\n## 核心规则\n\n规则\n")
        before = entry_status(entry)
        rename_module(entry, "核心规则", "通行规则")
        loaded = parse_canon_entry("world", render_canon_entry(entry))
        self.assertEqual(entry_status(loaded), before)
        custom = parse_canon_entry("world", "# T\n")
        add_module(custom, "核心规则", "text")
        set_section_text(custom, "核心规则", "用户自定义正文")
        custom = parse_canon_entry("world", render_canon_entry(custom))
        self.assertEqual(entry_status(custom)["missing_sections"], 7)


if __name__ == "__main__":
    unittest.main()
