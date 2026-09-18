# 测试有效性审查（2026-09-18）

审查前：99 个测试模块、672 项用例。通过测试方法清单、运行时代码引用、历史关键字及重复测试体检查筛选，再人工核对候选。未发现完全相同的测试方法体；不把名称相似、仅用于服务层或在特定平台跳过作为删除依据。

## 已删除的 4 项用例

| 原位置／用例 | 删除依据 |
| --- | --- |
| test_dashboard_page.py / test_meaningful_markdown_ignores_empty_template_headings | 总大纲已被故事规划表单替代；被测 `_has_meaningful_markdown` 没有运行时调用。一并移除死代码。章节预览测试保留。 |
| test_electron_migration_fixture.py / test_fixture_is_a_current_project_and_requires_no_migration | 不应要求冻结的旧项目样例始终匹配当前原生项目格式。当前新建项目与迁移行为仍由 test_project_migrations.py 覆盖。 |
| test_electron_migration_fixture.py / test_fixture_matches_recorded_project_surface | 对历史样例整套角色、记忆、伏笔、体系数据建立当前项目兼容契约，超出只读章节导入边界。对应功能自己的行为测试保留。 |
| test_electron_migration_fixture.py / test_fixture_exercises_extra_sections_and_managed_character_state | 锁定历史样例的固定章节附加段落和角色状态值；不是现行导入验收要求。章节解析、角色同步及源文件保护测试保留。 |

历史样例校验模块改名为 `test_legacy_import_fixture.py`，保留文件哈希与最小导入契约两项测试。移除无人使用的 `expected` 元数据，更新样例说明；冻结的 golden_project 文件未修改。

## 保留的保护

- 旧项目只读导入、源文件哈希不变、目标不能嵌套于源项目，以及源文件变化后的拒绝写入。
- 保存冲突、恢复、撤销、删除回收站、AI 审阅与过期上下文检查。
- 当前仍可调用的项目迁移、配置归一化、V2 服务与验证脚本测试；没有通过删测试改变功能范围。
- Windows 原生标题栏的条件测试。无窗口平台的跳过属于环境限制，不是失效。
- 时间线页面、事件编辑器和素材选择器的测试：相近名称覆盖的是不同交互，不合并删除。

审查后：99 个模块、668 项用例。已运行 `scripts/run_tests.py`：99 个模块、668 项用例，失败模块 0；其中 2 项 Windows 原生标题栏测试因 offscreen 环境跳过。`git diff --check` 通过，冻结样例正文及锁定文件哈希未修改。
