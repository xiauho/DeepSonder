# DeepSonder

DeepSonder 的 PySide6 桌面系列，面向长篇小说项目的本地写作、故事记忆、人物识别、关系梳理、一致性检查与 AI 辅助创作。

本仓库只以 `main.py` / `DeepSonder-PySide6.exe` 作为用户入口。PySide6 与 Electron 分别维护、测试和发布，不要求功能或更新同步。本系列能力状态记录在 [功能清单](docs/feature-status.json)，原 [功能对照](docs/feature-parity.json) 仅保留历史记录。

展示品牌使用 **DeepSonder**，程序与发布标识使用 **DeepSonder-PySide6**。当前版本见 [VERSION](VERSION)，变更记录见 [更新日志](CHANGELOG.md)。

## 下载首个测试版

[DeepSonder 0.1.0-beta（PySide6）](https://github.com/xiauho/DeepSonder/releases/tag/pyside6-v0.1.0-beta) 提供 Windows x64 便携 ZIP。完整解压后运行 `DeepSonder-PySide6.exe`，无需安装 Python。AI 功能需要另行安装并配置 DeepSeek Harness。

安装、校验、AI 配置和已知限制见 [本版发布说明](docs/releases/0.1.0-beta.md)。这是公开测试版，请先备份写作项目。

## 开发运行

需要 Python 3.12：

```powershell
.\run.bat
```

或手动安装后启动：

```powershell
python -m pip install -r requirements.txt
python main.py
```

## 快速导航与键盘操作

左侧“查找”或 `Ctrl+P` 可按标题、文件名和类别打开当前项目的章节与资料，留空优先显示最近访问。`Ctrl+Shift+P` 可搜索已有命令、查看快捷键及不可用原因。搜索框支持方向键选择、Enter 执行、Esc 关闭。

- `F6`：回到当前文档；预览模式保持只读。
- `Ctrl+Alt+F`：打开并搜索当前目录。
- `Ctrl+Alt+L`：定位当前文档并清除遮挡它的目录搜索。
- 专注模式下仍可使用快捷键；关闭快速入口不会退出专注模式。

## 设置修改

设置按写作、AI 与连接、外观、数据分类。修改会暂存为草稿，切换页面不会丢失，点击“保存设置”后生效。“恢复默认设置”同样先载入草稿；“放弃修改”恢复已保存配置。未保存草稿退出时会提示处理。

## 故事规划

资料栏“故事规划”提供故事核心、人物变化、整体走向、创作边界四项可选输入，无需编写 Markdown。“用于 AI 创作”默认关闭；保存并开启后，仅作为续写、扩写和一致性检查的方向参考。规划不作为既成事实，不得覆盖正文事实和本章规划；尚未实现的走向不构成一致性错误。规划超出上下文预算时会提示精简，不静默截断。

具体未来事件统一放在时间线，章节细节留在本章大纲／剧情简写。新项目不再创建“总大纲”和“后续剧情规划”，不提供旧资料迁移入口。故事规划保存在 `outline/story_plan.json`，使用现有保存、自动保存、外部修改冲突和 AI 过期检查流程。

## 时间轴与事件素材

点击资料栏“时间线”，中央工作区会显示所有未删除事件组成的纵向时间轴，包括计划中、已写入正文和已放弃事件。它按作者编排顺序等距展示，时间标签可以是“雨夜”“三天后”或留空；节点距离不表示实际时间跨度。

在时间轴中可新建、编辑、搜索事件，按状态、故事线、关联章节和大事件筛选。选中事件可查看详情、打开关联章节、调整顺序、删除或恢复。窄窗口使用“查看事件详情／返回时间轴”切换。筛选期间不允许调整全局顺序；“重置筛选”后可上移、下移或移到另一事件前后。切换章节再返回以及重启应用都会保留时间轴浏览状态。

“加入章节素材…”要求明确选择目标章节和用途，确认后打开该章，不自动生成正文。续写和扩写前也可选择最多 8 个事件，指定“背景事实”“本次展开”或“暂不揭示”。所选素材按时间轴顺序提供，生成正文不会自动更改事件状态。背景事实必须关联当前或前文章节，后文章节中的事件只能作为暂不揭示的约束。

事件保存在项目内 `canon/timeline_events.json`。原 `canon/timeline.md` 可通过时间轴顶部“时间线笔记”打开，新建时完全空白，不自动解析、覆盖或加入 AI 上下文。编辑事件会清除该事件已保存的素材用途，下次写作请重新选择。修改资料会使使用旧上下文的 AI 结果失效；素材超出预算会提示调整，不截断事件后继续生成。正文自动识别、多线时间轴和相对时间推算属于后续阶段。

## 导入旧版项目

在“文件 → 导入旧版项目…”中选择旧 Novalist 项目。DeepSonder-PySide6 会创建一个全新的项目，只复制：

- 项目名称；
- 作者；
- 按顺序排列的章节 Markdown 正文。

旧项目始终只读，不会被修改。旧记忆、人物识别、设定、关系图、AI 结果、缓存、提案、更新状态与回收站均不导入；需要时请在新项目中从正文重新提取。

## 测试与打包

```powershell
python scripts/run_tests.py
python -m pip install -r requirements-build.txt
.\scripts\build_windows.ps1 -PythonExecutable python
```

Windows 包为独立的 PyInstaller one-folder ZIP，不包含 Electron、Node.js、Sidecar 或旧自动更新器。详见 [打包说明](PACKAGING.md)。

## 数据与网络边界

项目与设置默认保存在本地。只有用户主动运行 AI 任务时，任务需要的正文和上下文才可能通过本机 `dsh` 发往其配置的服务。应用不提供旧版本自动更新或跨系列更新通道。

许可证与第三方说明见 [LICENSE](LICENSE)、[隐私说明](PRIVACY.md)、[第三方声明](THIRD_PARTY_NOTICES.md) 和 [许可证目录](licenses/README.md)。

## 文档导航

- [当前产品范围](PRD.md)
- [正文创作与文风审校](docs/writing-style-review.md)
- [贡献指南](CONTRIBUTING.md)与[安全政策](SECURITY.md)
- [历史文档](docs/archive/README.md)：旧 Novalist 发布记录、产品拆分记录及早期规划，仅供追溯。
