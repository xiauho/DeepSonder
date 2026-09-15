# DeepSonder-PySide6 Windows packaging

DeepSonder-PySide6 使用 Python 3.12 与 PyInstaller 生成独立的 one-folder Windows x64 ZIP。发布包不包含 Electron、Node.js、Sidecar、旧 Novalist 更新器、项目数据、用户配置或缓存。

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
