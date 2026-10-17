# DeepSonder 使用指南：下载安装与 DSH 配置

适用于 DeepSonder-PySide6 0.1.0-beta、Windows 10/11 x64。软件界面使用 **DeepSonder** 名称，程序文件名为 `DeepSonder-PySide6.exe`。本文按当前仓库界面编写；DSH 外部文档核对日期为 2026-09-20。

第一次使用的顺序是：**下载并解压软件 → 在电脑上安装 DSH、配置模型 → 在软件中连接 DSH → 测试并开始写作**。只使用本地编辑功能时，可以跳过 DSH 配置。

## 一、下载压缩安装包

### 1. 找到正确的文件

打开 [DeepSonder-PySide6 0.1.0-beta 发布页](https://github.com/xiauho/DeepSonder/releases/tag/pyside6-v0.1.0-beta)，展开页面底部的 **Assets**，下载：

- `DeepSonder-PySide6-v0.1.0-beta-windows-x64.zip`：可直接运行的便携版。
- `SHA256SUMS.txt`：用于核对下载文件是否完整。

`release-manifest.json` 是发布信息清单，可按需下载留存。不要把 **Source code (zip)** 或 **Source code (tar.gz)** 当成安装包；它们是源码。其他版本请从 [发布列表](https://github.com/xiauho/DeepSonder/releases)中选择标记为 PySide6 的版本，并以该版本说明为准。

### 2. 校验并完整解压

在下载目录空白处右键打开终端，或打开 PowerShell 后切换到下载目录，执行：

```powershell
Get-FileHash .\DeepSonder-PySide6-v0.1.0-beta-windows-x64.zip -Algorithm SHA256
```

将结果中的 Hash 与 `SHA256SUMS.txt` 中该 ZIP 对应的值比较，应完全一致（字母大小写不影响比较）。

右键 ZIP，选择“全部解压”，放到有写入权限的目录，例如 `D:\Apps\DeepSonder-PySide6`。进入解压后的文件夹，双击 `DeepSonder-PySide6.exe`。

**不要在压缩包内直接运行，也不要只把 EXE 拖出来。** 程序依赖同目录中的其他文件。便携版无需另装 Python；可以为 EXE 创建桌面快捷方式。本测试版未做 Windows 代码签名，遇到未知发布者提示时先核对来源和校验值，详见[发布说明](releases/0.1.0-beta.md)。

## 二、在电脑上安装和配置 DSH

DSH 是 DeepSeek Harness 的命令行入口。DeepSonder 调用它执行 AI 任务；模型、服务地址和 API 密钥由 Harness 管理。软件压缩包不包含 DSH、Node.js、模型或 API 密钥。

### 1. 安装 Node.js

从 [Node.js 官方下载页](https://nodejs.org/en/download)选择 Windows x64 的 LTS 安装程序，按安装向导完成安装，保留 npm 和 PATH 相关选项。若 DSH 提示运行时版本不满足要求，以所安装 DSH 的要求为准。

安装完成后，关闭旧终端，重新打开 PowerShell，逐条运行：

```powershell
node --version
npm.cmd --version
```

两条命令都应显示版本号。本文使用 `.cmd` 入口，可避免 PowerShell 优先选择 npm 的 `.ps1` 脚本而出现执行策略错误。

### 2. 安装 DSH

在 PowerShell 中执行：

```powershell
npm.cmd install -g @deepseek-ai/dsh
dsh.cmd --version
dsh.cmd --help
```

第一条命令需要联网；后两条用于确认安装入口和可用参数。官方 npm 包提供 `dsh` 命令，支持 `--profile headless`，参见 [DSH 命令行说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/README.md)。上游仍在快速迭代，本文不把某个未经本软件验证的 DSH 版本声明为兼容版本；最终以软件连接测试和短篇创作测试为准。

### 3. 让 Windows 找到 dsh

执行以下命令查看入口和 npm 全局安装目录：

```powershell
where.exe dsh.cmd
npm.cmd prefix -g
```

如果找不到 `dsh.cmd`，检查第二条命令输出的目录中是否有该文件。若有，可将该目录添加到当前用户的 PATH：

1. 在 Windows 开始菜单搜索“编辑账户的环境变量”。
2. 在“用户变量”中选择 `Path`，点击“编辑”。
3. 点击“新建”，填入 `npm.cmd prefix -g` 输出的目录，保留原有条目。
4. 确定保存，关闭并重新打开 PowerShell 和 DeepSonder，再执行 `where.exe dsh.cmd`。

也可以稍后在 DeepSonder 中直接填写 `dsh.cmd` 的绝对路径。路径以自己电脑的实际输出为准，不要照抄别人的用户名。

### 4. 在 Harness 中配置模型和密钥

先准备所使用模型服务的 API 密钥。然后在 PowerShell 中运行：

```powershell
dsh.cmd web
```

按终端显示的地址打开浏览器；官方默认地址为 `http://127.0.0.1:3080`。配置期间保持该终端运行。官方也提供 `npx @deepseek-ai/dsh web` 启动方式，见 [Harness 官方项目](https://github.com/deepseek-ai/deepseek-harness)。

在 Harness 网页的 **Settings → Models（设置 → 模型）** 中：

1. 使用 DeepSeek 服务时，在 DeepSeek 卡片填写 API key 并保存。
2. 使用其他服务时，通过 **Add provider** 添加服务；自定义网关使用 **Add a custom provider**，按服务商说明填写地址、协议、凭据和模型 ID。
3. 在模型选择器中选定已配置的模型。相关界面可能随 DSH 版本调整，参见[官方模型配置指南](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md)。

不要把 API key 填入 DeepSonder 的“命令”“启动参数”或“附加参数”。如果另外使用环境变量提供凭据，须确保启动 DeepSonder 的进程也能读取它；仅在某个终端里临时设置的变量通常不会传给从桌面启动的软件。

### 5. 验证 headless 模式

打开另一个 PowerShell 窗口，在一个专用的空测试目录中运行：

```powershell
dsh.cmd --profile headless "请只回复：连接成功"
```

正常情况下，DSH 返回“连接成功”并结束本次命令。该步骤会实际调用模型服务，可能消耗额度。`--version` 成功仅表示程序能启动，不能证明模型和密钥配置正确。

DeepSonder 使用的正是 headless 模式，因此网页聊天成功后仍应完成这一步。若提示缺少凭据或模型，请检查 headless 是否读取了与网页配置相同的 Harness 配置；尤其注意不同 Windows 账户、自定义 `DSH_HOME` 和不同 DSH 安装入口。

完成后可在运行网页服务的终端按 `Ctrl+C` 关闭它。DeepSonder 自行启动 DSH 命令，不需要把网页服务地址填入软件，也不要求该网页服务一直运行。

## 三、在 DeepSonder 中配置 DSH

### 1. 填写连接参数

打开 DeepSonder 的 **设置 → AI 与连接**，按下表填写：

| 界面字段 | 首次配置建议 | 说明 |
| --- | --- | --- |
| 命令 | `dsh` | 找不到时，改为 `where.exe dsh.cmd` 输出的完整路径 |
| 模型上下文窗口 | 自动 / 未确认（安全预算） | 确认模型输入与输出合计窗口后再填写真实值 |
| 上下文使用策略 | 均衡 · 日常创作 | 首次使用保留该选项 |
| 最长等待 | 600 秒 | 普通 AI 任务的等待设置，可选范围为 30–1800 秒 |

展开 **高级启动参数**：

| 界面字段 | 直接调用 dsh 时的填写方式 |
| --- | --- |
| 启动参数 | 留空 |
| 运行配置 | `headless（固定）`，无需填写 |
| 提示词传输 | 固定值，无需修改 |
| 附加参数 | 留空 |

“命令”只填命令名或文件路径，例如 `C:\Users\你的用户名\AppData\Roaming\npm\dsh.cmd`。**不要填整行 `dsh --profile headless`，不要在路径外添加引号。** 软件会自动添加运行配置和任务内容。填写模型上下文窗口只是调整软件的上下文预算，不会切换模型或扩大模型实际容量。

### 2. 测试并保存

1. 点击 **测试 dsh**，等待结果。
2. 成功提示应包含“dsh 可用，任务传递正常”，并确认文件业务传输可用。
3. 点击 **保存设置**，让配置正式生效。

测试使用当前页面中填写的草稿；**测试成功不等于已保存**。切换页面会保留草稿，“放弃修改”会恢复已保存设置。

测试会调用模型并检查临时任务文件读取，可能消耗服务额度。当前版本的连接探测使用较短时限（每次探测调用最多 15 秒），与普通任务的“最长等待”不同；将 600 秒调大不能延长连接探测时间。测试包含多个步骤，总耗时可能超过 15 秒。

### 3. 可选：使用 npx 启动

已安装 Node.js、希望通过 npx 运行 DSH 时，可以使用：

| 字段 | 内容 |
| --- | --- |
| 命令 | `npx.cmd`，或 `where.exe npx.cmd` 输出的绝对路径 |
| 启动参数 | `--yes @deepseek-ai/dsh` |
| 附加参数 | 留空 |

首次下载和初始化可能超过连接测试时限。先在终端完成以下命令，再到软件测试并保存：

```powershell
npx.cmd --yes @deepseek-ai/dsh --version
npx.cmd --yes @deepseek-ai/dsh --profile headless "请只回复：连接成功"
```

日常使用优先选择前面的全局安装方式，并确保配置模型与运行软件时使用同一个 DSH 安装入口。

## 四、完成第一次写作测试

1. 新建一个测试项目，将项目保存在独立于软件安装目录的位置。
2. 新建章节，输入一小段正文或剧情简写，保存后尝试一次续写或扩写。
3. 阅读生成结果，确认内容合适后再采用；测试时也要检查最终写入位置。
4. 再按需使用人物资料、世界观、故事规划、记忆更新和一致性检查。

生成期间如果修改了相关正文或资料，旧结果可能因上下文过期而不能采用，请按提示重新生成。故事规划需保存并开启“用于 AI 创作”后才参与相关任务。更详细的创作说明见[正文创作与文风审校](writing-style-review.md)。

## 五、常见问题

| 现象 | 处理方法 |
| --- | --- |
| ZIP 中运行失败，或提示缺少依赖文件 | 完整解压后运行，保持程序目录内文件齐全 |
| `node` / `npm.cmd` 无法识别 | 检查 Node.js 安装与 PATH，重新打开终端 |
| PowerShell 提示不允许运行 `.ps1` | 使用本文的 `npm.cmd`、`npx.cmd`、`dsh.cmd` 入口 |
| 软件提示“找不到命令” | 用 `where.exe dsh.cmd` 定位入口，填写绝对路径；修改 PATH 后重启软件 |
| 终端成功，软件失败 | 检查是否使用同一账户、同一 DSH 入口及相同环境变量；命令框不要包含参数 |
| 缺少凭据、401 或模型不存在 | 回 Harness 核对 API key、服务地址与模型 ID，再运行 headless 测试 |
| DSH 能启动，但未收到任务或任务探测失败 | 清空不必要的启动／附加参数；先验证本文的 headless 命令，再运行软件测试 |
| 连接测试超时 | 先完成终端初始化，检查网络与模型响应速度；普通任务等待设置不控制连接探测时限 |
| 文件业务传输失败 | 检查 DSH 是否能读取隔离临时任务文件，以及模型能否按要求调用工具；仅能聊天还不足以通过测试 |
| 普通创作任务超时 | 先缩短测试内容，核对模型服务状态，再按需调高“最长等待”并保存 |
| 测试通过，重开软件后设置丢失 | 确认测试后点击了“保存设置” |

## 六、保存、备份与更换版本

请定期备份整个写作项目目录。软件配置位于 `%APPDATA%\DeepSonder\PySide6\config.json`，缓存位于 `%LOCALAPPDATA%\DeepSonder\PySide6`；这些位置与 Harness 的模型凭据配置分别管理。

更换软件版本前退出程序并备份项目，将新 ZIP 解压到新的程序目录，再打开原来的本系列项目。本版不提供自动更新。旧 Novalist 项目请通过“文件 → 导入旧版项目…”创建新项目，仅导入名称、作者与按顺序排列的章节正文。

本地编辑使用本地文件；AI 任务会按需将正文和上下文交给 DSH 所配置的服务。反馈问题时附软件版本、DSH 版本、复现步骤和脱敏错误，勿公开 API 密钥与私稿。详见[隐私说明](../PRIVACY.md)。
