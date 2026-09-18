# DeepSonder-PySide6 Windows packaging

DeepSonder-PySide6 使用 Python 3.12 与 PyInstaller 生成独立的 one-folder Windows x64 ZIP。发布包不包含 Electron、Node.js、Sidecar、旧 Novalist 更新器、项目数据、用户配置或缓存。

所有命令从当前仓库根目录执行。版本由 [VERSION](VERSION) 提供；发行标识使用 `DeepSonder-PySide6`，Git 标签使用 `pyside6-v<版本>`。旧 `v2.*` 标签属于历史系列。

## 本地构建

```powershell
python -m pip install -r requirements.txt -r requirements-build.txt
.\scripts\build_windows.ps1 -PythonExecutable python
```

构建脚本默认先运行完整 Python 测试，然后：

1. 通过 `deepsonder_pyside6.spec` 生成 `dist/DeepSonder-PySide6/`；
2. 运行 `DeepSonder-PySide6.exe --self-test`；
3. 将许可证和隐私文件放入包内；
4. 生成 `dist/release-pyside6/DeepSonder-PySide6-v<version>-windows-x64.zip`；
5. 写入独立的 `release-manifest.json` 与 `SHA256SUMS.txt`。

此测试系列没有自动更新协议。`release-manifest.json` 仅用于本系列构建审计，不会被 Electron 系列消费，也不会触发旧版就地升级。

构建不会自动发布 GitHub Release，也不会自动修改 [更新日志](CHANGELOG.md)。正式发布前，将已验证的变更从 `Unreleased` 整理为对应版本并核对标签；历史安装说明见 [档案](docs/archive/README.md)，不用于当前安装。
