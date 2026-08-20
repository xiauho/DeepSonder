# Novalist

Novalist 是一款面向长篇小说创作的本地桌面工具，使用 PySide6 构建，并可通过 `dsh` 的 `headless` 模式调用 DeepSeek Harness 完成续写、一致性检查、章节摘要和故事状态更新。

> **第三方项目声明**：Novalist 是独立开发的第三方开源工具，不是 DeepSeek 或 DeepSeek Harness 的官方产品，与其不存在隶属、合作、授权、认证或背书关系。“DeepSeek Harness”仅用于说明兼容性和所依赖的外部工具。

## 项目定位

- 独立桌面应用，而不是加载到 Harness Cordis 插件树中的原生插件
- 小说、设定和记忆文件默认保存在本地 Markdown/JSON 文件中
- Novalist 不直接接收或保存 DeepSeek API 密钥；凭据和模型连接由用户安装的 `dsh` 管理
- 本仓库仅发布源码，不提供 EXE 或其他预编译二进制文件

## 主要功能

- **沉浸式写作台**：低对比深色/浅色主题、蓝色强调色、可收起侧栏、专注模式、查找替换和未保存状态提示
- **写作统计**：实时显示字数、段落、预计阅读时长和光标位置
- **安全保存**：定时自动保存，切换资料时自动落盘，退出前检查未保存内容
- **故事资料库**：管理大纲、章节、角色、世界观、战力体系和时间线
- **故事雷达**：汇总章节目标、相关人物、故事状态、摘要和待回收伏笔
- **AI 辅助**：通过 `dsh --profile headless` 续写、检查一致性、生成摘要和更新记忆
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

## 配置 DeepSeek Harness

首次运行会由 `config.example.json` 生成本机的 `config.json`。后者已被 Git 忽略，不应提交到公开仓库。

如果系统已经能直接执行 `dsh`，保持默认设置即可：

```json
{
  "dsh_command": "dsh",
  "dsh_launcher_args": [],
  "dsh_profile": "headless",
  "dsh_timeout": 180,
  "dsh_extra_args": []
}
```

也可以通过官方 npm 包运行。在 Windows 的“偏好设置”中将命令设为 `npx.cmd`，启动参数设为 `--yes @deepseek-ai/dsh`；对应 JSON 为：

```json
{
  "dsh_command": "npx.cmd",
  "dsh_launcher_args": ["--yes", "@deepseek-ai/dsh"],
  "dsh_profile": "headless"
}
```

macOS/Linux 通常使用 `npx` 而不是 `npx.cmd`。API 密钥应按照 Harness 官方文档配置，不要写入 Novalist 配置、源码或 Git 提交。

## 基本使用

1. 启动后打开 `projects/demo_novel`，或新建自己的项目。
2. 从左侧打开章节，在中间编辑器中写作。
3. 使用“AI 续写”“检查设定”或“更新记忆”时，首次调用会显示数据处理告知。
4. 审核 AI 输出后再保存、采用或公开。
5. 通过“文件 → 导出全书”生成 `.md` 或 `.txt` 文件。

## 常用快捷键

| 操作 | 快捷键 |
|---|---|
| 保存 | `Ctrl+S` |
| 新建章节 | `Ctrl+N` |
| 查找与替换 | `Ctrl+F` |
| 进入/退出专注模式 | `Ctrl+K` 或 `F11`；专注模式中也可按 `Esc` |
| 显示/隐藏左侧工作区 | `Ctrl+Shift+L` |
| AI 续写 | `Ctrl+Enter` |
| 一致性检查 | `Ctrl+Shift+C` |
| 更新故事记忆 | `Ctrl+Shift+M` |
| 显示/隐藏故事雷达 | `Ctrl+Shift+I` |
| 显示/隐藏 AI 记录 | `Ctrl+J` |
| 导出全书 | `Ctrl+Shift+E` |

## 数据与 AI 输出说明

Novalist 本身不提供云服务或遥测。普通编辑内容保存在用户选择的本地项目目录中。

当用户主动调用 AI 功能时，当前章节以及完成任务所需的大纲、角色、世界观、摘要和故事状态可能被传递给本机配置的 `dsh`，并由其连接的 AI 服务处理。请勿提交无权处理的作品、商业秘密或敏感个人信息。

AI 输出可能存在事实错误、遗漏、不当内容或与第三方作品相似的表达。用户应在采用、发布或商业使用前自行审核。更完整的说明见 [PRIVACY.md](PRIVACY.md)。

## AI 辅助与权利声明

本项目在开发过程中使用了 AI 辅助工具，所有公开内容均由项目维护者审核。如您认为本项目中的代码、文档、图标或演示内容侵犯了您的合法权益，或存在其他合规问题，请通过 GitHub Issue 联系并提供具体材料。维护者将在核实后及时采取更正、替换、移除相关内容或其他适当措施。

本声明不构成对任何侵权或违规行为的免责。

## 开发与测试

```bash
python -m unittest discover -s tests
python -m compileall -q main.py core ui
```

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
