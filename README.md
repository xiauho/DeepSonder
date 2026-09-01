# Novalist

> 当前发布版本：`v2.0.8-beta`。本版本为 Windows x64 便携包加入事务式自动安装更新、启动自检与失败回滚，属于公开预览版；请在真实创作项目中使用前先完成一次本机 DSh 实测并保留独立备份。

Novalist 是一款面向长篇小说创作的本地桌面工具，使用 PySide6 构建，并可通过 `dsh` 的 `headless` 模式调用 DeepSeek Harness 完成章节扩写、一致性检查、章节摘要和故事状态更新。

> **第三方项目声明**：Novalist 是独立开发的第三方开源工具，不是 DeepSeek 或 DeepSeek Harness 的官方产品，与其不存在隶属、合作、授权、认证或背书关系。“DeepSeek Harness”仅用于说明兼容性和所依赖的外部工具。

## 项目定位

- 独立桌面应用，而不是加载到 Harness Cordis 插件树中的原生插件
- 小说、设定和记忆文件默认保存在本地 Markdown/JSON 文件中
- Novalist 不直接接收或保存 DeepSeek API 密钥；凭据和模型连接由用户安装的 `dsh` 管理
- 本仓库发布源码，并从 `v2.0.6-beta` 开始提供官方 Windows x64 便携 ZIP

## 主要功能

- **沉浸式写作台**：蓝白浅色主题（可切换蓝黑深色）、统一一级导航、可收起侧栏、专注模式、查找替换和未保存状态提示
- **写作统计**：实时显示字数、段落、预计阅读时长和光标位置
- **安全保存**：定时自动保存，切换资料时自动落盘，退出前检查未保存内容
- **故事资料库**：管理大纲、章节、项目写作风格、角色、世界观、战力体系和时间线
- **故事雷达**：汇总章节目标、相关人物、故事状态、摘要和待回收伏笔
- **项目概览**：集中展示最近章节、项目准备情况和基于当前数据生成的下一步建议
- **一致性报告**：按中文问题类型和严重级别展示证据、位置与建议，并可从报告页选择章节重新检查
- **安全定位与修复**：对存在唯一正文锚点的问题支持跳转；仅对可安全替换的正文问题开放 AI 修复预览
- **AI 辅助**：通过 `dsh --profile headless` 扩写章节、检查一致性、修复受支持的问题、生成摘要和更新记忆；扩写与续写可自动应用项目级写作风格
- **内置更新**：从“帮助”菜单检查 GitHub Release，可安全下载并验证 Windows x64 更新包；支持自动安装的便携版可由独立更新器备份、替换、启动自检并在失败时回滚
- **首次使用告知**：调用 AI 前明确提示可能发送的数据范围和 AI 输出风险
- **整书导出**：导出为 Markdown 或纯文本

## 环境要求

- Python 3.10 或更高版本
- Windows 10/11（`run.bat`）；其他系统可使用 Python 手动启动
- 使用 AI 功能时，需要另行安装并配置 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)

DeepSeek Harness 当前仍处于开发者预览阶段，可能发生不兼容变更。遇到调用问题时，请先核对 Harness 的最新官方说明和命令行参数。

当前命令调用形式已对 `@deepseek-ai/dsh 0.1.0-rc.7` 的公开 CLI 帮助完成兼容检查；由于真实生成需要用户自己的凭据和额度，仓库测试不会发起真实 API 请求。

## Windows 启动

下载或克隆仓库后，双击：

```bat
run.bat
```

脚本会创建项目专用的 `.venv` 环境并安装 `requirements.txt` 中声明的依赖。它不会安装 DeepSeek Harness，也不会读取或写入 API 密钥。

## 手动启动

```bash
python -m venv .venv
```

Windows：

```bat
.venv\Scripts\activate
python -m pip install -r requirements.txt
python main.py
```

macOS/Linux：

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

## Windows 打包

从 `v2.0.6-beta` 开始，项目提供无需预装 Python 的 Windows x64 目录包基线。开发者安装 `requirements-build.txt` 后可运行 `scripts/build_windows.ps1`；脚本会执行全量测试、构建 PyInstaller 包、运行无界面自检，并在 `dist/release` 中生成 ZIP、发布清单和 SHA-256 文件。详细边界见 `PACKAGING.md`。

## 配置 DeepSeek Harness

Novalist 将本机设置保存在当前用户的应用数据目录中：Windows 为 `%APPDATA%\Novalist\config.json`，macOS 为 `~/Library/Application Support/Novalist/config.json`，Linux 为 `$XDG_CONFIG_HOME/Novalist/config.json`（未设置时使用 `~/.config/Novalist/config.json`）。

从旧版本升级时，如果新位置尚无配置，Novalist 会读取程序目录中的旧 `config.json`，将规范化后的设置复制到新位置，并保留旧文件作为备份。`config.example.json` 仅作为配置格式参考，不应写入 API 密钥或提交个人配置。

未来的更新下载缓存预留在本机缓存目录中；Windows 对应 `%LOCALAPPDATA%\Novalist\updates`。缓存不包含小说项目，删除后可重新下载。

“帮助 → 检查更新”会读取 `xiauho/novalist` 的公开 GitHub Release。设置中可自愿开启启动检查；开启后最多每 24 小时检查一次。从 `v2.0.7-beta` 开始，发现新版本后可将唯一匹配的 Windows x64 软件包下载到 `%LOCALAPPDATA%\Novalist\updates`，并核对 GitHub 摘要、发布清单、文件大小、SHA-256、包内路径、版本和全部 ZIP 文件 CRC。

`v2.0.8-beta` 便携包增加独立的 `NovalistUpdater.exe` 和逐文件 `package-files.json`。用户确认安装后，主程序只把当前安装中通过受管文件校验的更新器复制到缓存并退出；更新器再次验证缓存状态、ZIP、软件包清单和逐文件哈希，只备份及替换清单内的程序文件，保留项目和其他非受管内容。新版必须通过 `Novalist.exe --self-test` 才会启动，否则恢复旧版本。自动安装仅支持可写的 Windows x64 便携目录，不申请管理员权限，无法满足条件时仍保留手动解压方案。由于 `v2.0.7-beta` 本身没有独立更新器，从该版本升级到 `v2.0.8-beta` 仍需手动安装；安装 `v2.0.8-beta` 后才能自动安装后续兼容版本。

Novalist 的 AI 配置只针对 DeepSeek Harness 的 `dsh --profile headless` 入口，不提供其他模型供应商、API Key 或模型列表配置。凭据和模型连接由 Harness 自己管理。

如果系统已经能直接执行 `dsh`，保持默认设置即可：

```json
{
  "dsh_command": "dsh",
  "dsh_launcher_args": [],
  "dsh_profile": "headless",
  "dsh_timeout": 600,
  "dsh_extra_args": [],
  "dsh_prompt_transport": "auto",
  "dsh_file_prompt_budget": 48000
}
```

默认的 `auto` 模式会继续通过命令行传递较短任务；较长任务会先验证
Harness 是否能够读取 Novalist 隔离目录中的临时 UTF-8 任务文件，验证成功后
改用文件桥接，以避开 Windows 进程参数长度限制。任务文件在成功、失败、超时
或取消后都会尽力删除；验证失败时自动保持原有的 24000 字符兼容预算。
如需强制使用旧行为，可将 `dsh_prompt_transport` 设为 `argv`。

也可以通过官方 npm 包运行。在 Windows 的“偏好设置”中将命令设为 `npx.cmd`，启动参数设为 `--yes @deepseek-ai/dsh`；对应 JSON 为：

```json
{
  "dsh_command": "npx.cmd",
  "dsh_launcher_args": ["--yes", "@deepseek-ai/dsh"],
  "dsh_profile": "headless",
  "dsh_prompt_transport": "auto"
}
```

macOS/Linux 通常使用 `npx` 而不是 `npx.cmd`。API 密钥应按照 Harness 官方文档配置，不要写入 Novalist 配置、源码或 Git 提交。

## 基本使用

1. 启动后打开 `projects/demo_novel`，或新建自己的项目。
2. 从左侧打开章节，在中间编辑器中写作。
3. 使用“AI 扩写”“检查设定”或“更新记忆”时，首次调用会显示数据处理告知；检查报告页可直接选择其他章节运行检查。
4. 一致性问题只有在正文原句能够唯一定位且适合安全替换时才开放“AI 修复”；修复结果必须经过预览确认，且不会自动保存章节。
5. 审核 AI 输出后再保存、采用或公开。
6. 通过“文件 → 导出全书”生成 `.md` 或 `.txt` 文件。

## 常用快捷键

| 操作 | 快捷键 |
|---|---|
| 保存 | `Ctrl+S` |
| 新建章节 | `Ctrl+N` |
| 查找与替换 | `Ctrl+F` |
| 进入/退出专注模式 | `Ctrl+K` 或 `F11`；专注模式中也可按 `Esc` |
| 显示/隐藏左侧工作区 | `Ctrl+Shift+L` |
| AI 扩写 | `Ctrl+Enter` |
| 一致性检查 | `Ctrl+Shift+C` |
| 更新故事记忆 | `Ctrl+Shift+M` |
| 显示/隐藏故事雷达 | `Ctrl+Shift+I` |
| 显示/隐藏 AI 记录 | `Ctrl+J` |
| 导出全书 | `Ctrl+Shift+E` |

## 数据与 AI 输出说明

Novalist 本身不提供云服务或遥测。普通编辑内容保存在用户选择的本地项目目录中。

当用户主动调用 AI 功能时，当前章节以及完成任务所需的大纲、写作风格指南、角色、世界观、摘要和故事状态可能被传递给本机配置的 `dsh`，并由其连接的 AI 服务处理。请勿提交无权处理的作品、商业秘密或敏感个人信息。

`dsh` 在 Novalist 创建的隔离临时目录中运行，不能直接读取或修改项目文件；较短任务通过命令行参数传入，较长任务可在能力验证成功后通过该目录中的短期任务文件传入。上下文受长度预算约束，超出时会按重要性裁剪（优先保留章节大纲、正文结尾与故事状态），并在提示词中明确标注截断，而不是让调用失败。

AI 输出可能存在事实错误、遗漏、不当内容或与第三方作品相似的表达。用户应在采用、发布或商业使用前自行审核。更完整的说明见 [PRIVACY.md](PRIVACY.md)。

## AI 辅助与权利声明

本项目在开发过程中使用了 AI 辅助工具，所有公开内容均由项目维护者审核。如您认为本项目中的代码、文档、图标或演示内容侵犯了您的合法权益，或存在其他合规问题，请通过 GitHub Issue 联系并提供具体材料。维护者将在核实后及时采取更正、替换、移除相关内容或其他适当措施。

本声明不构成对任何侵权或违规行为的免责。

## 开发与测试

```bash
python -m unittest discover -s tests
python -m compileall -q main.py core ui
```

使用本机已经配置的 DSh 运行长上下文真实验收（会发起少量真实模型请求，
只发送随机标记和合成文本，不读取小说项目）：

```bash
python scripts/verify_dsh_prompt_transport.py --long-chars 45000
```

可追加 `--report <路径>` 保存不含提示词正文的 JSON 验收报告。

参与贡献前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题请参阅 [SECURITY.md](SECURITY.md)。

## 项目结构

```text
main.py
run.bat
requirements.txt
config.example.json
assets/
core/
ui/
projects/demo_novel/
tests/
```

## 许可

Novalist 源码采用 [MIT License](LICENSE)。PySide6/Qt、Python 等第三方组件仍适用各自的许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
