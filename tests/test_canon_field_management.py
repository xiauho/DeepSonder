import unittest
from core.canon_schema import (parse_canon_entry, render_canon_entry, add_module,
    set_section_field, set_group_field, rename_module)
from core.canon_fields import (add_field, rename_field, move_field, delete_field,
    add_group, rename_group, move_group, delete_group)
from tests.test_canon_schema import character_text


class CanonFieldManagementTests(unittest.TestCase):
    def reload(self, entry):
        return parse_canon_entry(entry.kind, render_canon_entry(entry))

    def test_empty_custom_fields_crud_and_order_survive_reopen(self):
        entry = parse_canon_entry("world", "# T\n\n## 原文\n\n保留\r\n")
        module = add_module(entry, "档案", "fields")
        for name in ("A", "B", "C"):
            add_field(entry, module.module_id, name)
            set_section_field(entry, module.module_id, name, name + "值")
        rename_field(entry, module.module_id, "B", "新B")
        move_field(entry, module.module_id, "C", -1)
        delete_field(entry, module.module_id, "A")
        loaded = self.reload(entry)
        fields = loaded.section("档案").fields
        self.assertEqual(list(fields), ["C", "新B"])
        self.assertEqual(list(fields.values()), ["C值", "B值"])
        for key in list(fields):
            delete_field(loaded, module.module_id, key)
        empty = self.reload(loaded).section("档案")
        self.assertEqual(empty.role, "fields")
        self.assertEqual(empty.fields, {})
        self.assertIn("## 原文\n\n保留\r\n", render_canon_entry(entry))

    def test_template_fields_are_protected_but_custom_fields_are_managed(self):
        entry = parse_canon_entry("character", character_text())
        section = entry.section("基础档案")
        before = render_canon_entry(entry)
        for operation in (
            lambda: rename_field(entry, section.module_id, "姓名", "名字"),
            lambda: delete_field(entry, section.module_id, "姓名"),
            lambda: move_field(entry, section.module_id, "姓名", 1),
            lambda: add_field(entry, section.module_id, "姓名"),
        ):
            with self.assertRaises(ValueError): operation()
            self.assertEqual(render_canon_entry(entry), before)
        add_field(entry, section.module_id, "偏好")
        add_field(entry, section.module_id, "忌讳")
        move_field(entry, section.module_id, "忌讳", -1)
        self.assertEqual(list(self.reload(entry).section("基础档案").fields)[-2:], ["忌讳", "偏好"])

    def test_group_crud_retains_fields_extras_and_template_schema_after_rename(self):
        entry = parse_canon_entry("character", character_text())
        section = entry.section("能力档案")
        rename_module(entry, section.module_id, "成长路径")
        add_field(entry, section.module_id, "施展姿态", "潮汐感知")
        set_group_field(entry, section.module_id, "潮汐感知", "施展姿态", "闭目")
        rename_group(entry, section.module_id, "潮汐感知", "感潮")
        move_group(entry, section.module_id, "感潮", 1)
        add_group(entry, section.module_id, "新能力")
        self.assertIn("所属体系", section.groups[-1].fields)
        delete_group(entry, section.module_id, "忆潮")
        loaded = self.reload(entry).section("成长路径")
        self.assertEqual([g.name for g in loaded.groups], ["感潮", "新能力"])
        self.assertEqual(loaded.groups[0].fields["施展姿态"], "闭目")
        self.assertEqual(loaded.schema_key, "能力档案")

    def test_custom_empty_group_and_last_group_deletion_keep_group_type(self):
        entry = parse_canon_entry("world", "# T\n")
        module = add_module(entry, "编年", "group")
        add_group(entry, module.module_id, "空条目")
        loaded = self.reload(entry)
        self.assertEqual(loaded.section("编年").groups[0].fields, {})
        delete_group(loaded, module.module_id, "空条目")
        self.assertEqual(self.reload(loaded).section("编年").role, "group")

    def test_group_field_order_rename_and_delete_do_not_touch_another_group(self):
        entry = parse_canon_entry("world", "# T\n")
        module = add_module(entry, "编年", "group")
        for group in ("甲", "乙"):
            add_group(entry, module.module_id, group)
            for name in ("时间", "事件"):
                add_field(entry, module.module_id, name, group)
                set_group_field(entry, module.module_id, group, name, group + name)
        rename_field(entry, module.module_id, "事件", "事迹", "甲")
        move_field(entry, module.module_id, "事迹", -1, "甲")
        delete_field(entry, module.module_id, "时间", "甲")
        groups = self.reload(entry).section("编年").groups
        self.assertEqual(groups[0].fields, {"事迹": "甲事件"})
        self.assertEqual(groups[1].fields, {"时间": "乙时间", "事件": "乙事件"})

    def test_invalid_names_duplicates_and_boundaries_leave_source_unchanged(self):
        entry = parse_canon_entry("world", "# T\n")
        module = add_module(entry, "字段", "fields")
        add_field(entry, module.module_id, "A")
        original = render_canon_entry(entry)
        for name in ("", "带：冒号", "换\n行", "<!-- x -->", "跨\u2028行", "a"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError): add_field(entry, module.module_id, name)
                self.assertEqual(render_canon_entry(entry), original)
        with self.assertRaises(ValueError): move_field(entry, module.module_id, "A", -1)
        self.assertEqual(render_canon_entry(entry), original)
        group = add_module(entry, "分组", "group")
        add_group(entry, group.module_id, "A")
        original = render_canon_entry(entry)
        for operation in (lambda: add_group(entry, group.module_id, "a"),
                          lambda: move_group(entry, group.module_id, "A", 1),
                          lambda: rename_group(entry, group.module_id, "A", "换\n行")):
            with self.assertRaises(ValueError): operation()
            self.assertEqual(render_canon_entry(entry), original)

    def test_readonly_and_raw_modules_reject_management(self):
        entry = parse_canon_entry("character", character_text())
        for heading in ("当前剧情状态（DeepSonder 同步）", "作者自由备注", "关键关系"):
            before = render_canon_entry(entry)
            for operation in (lambda: add_field(entry, heading, "测试"), lambda: add_group(entry, heading, "测试")):
                with self.assertRaises(ValueError): operation()
            self.assertEqual(render_canon_entry(entry), before)


if __name__ == "__main__":
    unittest.main()
