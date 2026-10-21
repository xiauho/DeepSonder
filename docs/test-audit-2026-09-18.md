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

## 附：未复现的偶发失败（2026-10-20 追加）

`test_workspace_persistence` 整模块运行时观测到 **1 次** `failures=1`（`Ran 14 tests ... FAILED (failures=1)`）；当时未保留 traceback，其后未能复现。已投入的定位与结论：

- 逐用例隔离重复 57 次、完整模块运行 13 轮、同一模块双进程并发 2 轮、滚动/光标恢复路径直测 12 次，累计约 251 次用例执行，均通过。观测率已压到 **≤0.4% 每次执行**；在该量级下继续盲跑需要约 575 次执行（≈40 轮整模块）才有 90% 把握复现，收益不足，暂不投入。
- **已否证**：滚动/光标恢复竞态（直测 12 次数值完全一致）；并发同模块跨进程干扰（双进程实验通过）。
- **仍存疑但无证据**：瞬时文件或校验错误命中"成功断言"。该模块每个用例都断言 `_load_project(...)` 成功，而 `_load_project(quiet=True)` 会吞掉异常原因只返回 `False`，因此这类失败表现为无法解释的断言失败。为此在该模块的加载助手加入 `load_failure_detail`，失败时输出项目路径、`project.json` 状态、章节数与状态栏消息，便于下次直接区分文件错误与时序问题。
- **相关性**：唯一失败那轮是耗时最长的一次（270s，处于并发负载下）；此后所有快轮（115–180s）全部通过，更像负载敏感的时序问题而非文件错误。
- **复发时的正确做法**：不要截断测试输出。用 `python -m unittest tests.test_workspace_persistence -v 2>&1 | Tee-Object build\_ws.log` 完整留档（本次详情正是被 `Select-Object -Last N` 截断而丢失）；若能复现，优先在负载下重复运行。
