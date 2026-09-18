# DeepSonder-PySide6 Windows packaging

DeepSonder-PySide6 使用 Python 3.12 与 PyInstaller 生成独立的 one-folder Windows x64 ZIP。发布包不包含 Electron、Node.js、Sidecar、旧 Novalist 更新器、项目数据、用户配置或缓存。

所有命令从当前仓库根目录执行。版本由 [VERSION](VERSION) 提供；发行标识使用 `DeepSonder-PySide6`，Git 标签使用 `pyside6-v<版本>`。旧 `v2.*` 标签属于历史系列。

## 本地构建

```powershell
python -m pip install -r requirements.txt -r requirements-build.txt
.\scripts\build_windows.ps1 -PythonExecutable python
```

构建前须提交所有改动并保持工作区干净，确认本系列必需功能均为 supported。构建脚本默认先运行完整 Python 测试，然后：

1. 通过 `deepsonder_pyside6.spec` 生成 `dist/DeepSonder-PySide6/`；
2. 运行 `DeepSonder-PySide6.exe --self-test`；
3. 将许可证和隐私文件放入包内；
4. 生成 `dist/release-pyside6/DeepSonder-PySide6-v<version>-windows-x64.zip`；
5. 写入含源码提交与构建环境的 `release-manifest.json` 和 `SHA256SUMS.txt`；
6. 审计 ZIP 完整性、版本、校验值和必需文件，拒绝个人项目、配置、其他系列运行时或路径穿越。

此测试系列没有自动更新协议。`release-manifest.json` 仅用于本系列构建审计，不会被 Electron 系列消费，也不会触发旧版就地升级。

构建不会自动发布 GitHub Release，也不会自动修改 [更新日志](CHANGELOG.md)。正式发布前，将已验证的变更从 `Unreleased` 整理为对应版本并核对标签；历史安装说明见 [档案](docs/archive/README.md)，不用于当前安装。


## GitHub 发布

首次版本：`0.1.0-beta`，发布分支 `codex/release-pyside6-v0.1.0-beta`，标签 `pyside6-v0.1.0-beta`。保留历史标签，不把本系列资产添加到 Novalist 或 Electron Release。

完整测试已在同一代码版本通过时，可用 `-SkipTests` 避免重复测试；它不跳过功能门槛、干净工作区检查、打包自检和资产审计。源码或运行逻辑变化后必须重新验证。

发布顺序：提交发布材料 → 构建并校验 → 推送发布分支和带注释标签 → 等待 CI 与标签打包检查通过 → 创建草稿 Release 并上传 ZIP、SHA256SUMS.txt、release-manifest.json → 核对资产后公开为 prerelease。版本说明使用 `docs/releases/<version>.md`。不使用历史自动更新机制。

本系列的标签打包工作流会上传 Actions artifact；公开 Release 由维护者显式创建，避免把失败或不完整的包自动发布。
