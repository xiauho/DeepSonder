# Novalist v2.0.7-beta

## 本次版本

- 新增“安全下载更新”：发现更高版本后，可直接在 Novalist 内将官方 Windows x64 软件包下载到当前用户的更新缓存，不再必须先打开浏览器手动下载。
- 严格匹配目标版本唯一的 `Novalist-v<version>-windows-x64.zip` 和 `release-manifest.json`，缺失、重复或平台不符时停止处理。
- 发布清单升级为 schema v2；下载前后会交叉核对 GitHub Release Asset 提供的文件大小和 SHA-256 摘要，以及清单中的版本、通道、平台、架构、文件名、大小和哈希。
- 下载在后台进行，提供进度显示和取消操作；未完成或校验失败的 `.part` 文件会被清理。
- 更新包通过验证后保存为 `.verified.zip`，并写入独立的验证状态记录；缓存位置为 `%LOCALAPPDATA%\Novalist\updates`。
- 增加 ZIP 安全检查：拒绝路径穿越、绝对路径、Windows 保留名称、重复路径、符号链接、重解析点、加密文件、异常文件数量、异常展开大小、缺少运行文件和包内版本不一致。
- 对 ZIP 内所有文件执行完整解压和 CRC 校验，避免损坏的程序或运行库被标记为已验证。
- Windows 打包清单支持配置并校验最低更新器版本；最低版本高于目标版本时构建会立即停止。

## 下载与校验

1. 在旧版 Novalist 中打开“帮助 → 检查更新”。
2. 发现 `v2.0.7-beta` 后选择“安全下载更新”，等待下载和安全校验完成。
3. 校验成功后，Novalist 会打开缓存目录并显示 `.verified.zip` 的完整路径。
4. 关闭 Novalist，将 ZIP 完整解压到新的可写目录，再运行其中的 `Novalist.exe`。

也可以从 GitHub Release 手动下载以下三个文件：

- `Novalist-v2.0.7-beta-windows-x64.zip`
- `SHA256SUMS.txt`
- `release-manifest.json`

用户配置、小说项目、虚拟环境和更新缓存不会写入发布包。

## 预览版限制

- 本版本只负责安全下载、校验和缓存，不会自动关闭程序、覆盖安装目录、执行下载文件或失败回滚。
- 发布清单尚未使用独立 Ed25519 密钥签名；当前信任边界依赖 GitHub HTTPS、仓库 Release 权限和 GitHub 提供的资源摘要。
- 程序尚未进行 Windows Authenticode 代码签名，首次运行时 Windows 可能显示来源或安全提示。
- 本版本仍是便携目录包，不提供安装器、开始菜单快捷方式或自动卸载流程。
- 本版本仍依赖用户自行安装和配置的 DSh / DeepSeek Harness；AI 输出和对项目内容的修改均需人工审核。
- 本版本为公开 beta 预览版，正式写作前仍建议保留独立备份。
