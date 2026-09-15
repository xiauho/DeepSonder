# DeepSonder-PySide6

DeepSonder 的 PySide6 桌面系列，面向长篇小说项目的本地写作、故事记忆、人物识别、关系梳理、一致性检查与 AI 辅助创作。

本仓库只以 `main.py` / `DeepSonder-PySide6.exe` 作为用户入口。Electron 系列在独立工作区 `D:\GitHub-store\DeepSonder` 维护，两系列功能契约记录在 `docs/feature-parity.json`。

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

## 导入旧版项目

在“文件 → 导入旧版项目…”中选择旧 Novalist 项目。DeepSonder-PySide6 会创建一个全新的项目，只复制：

- 项目名称；
- 作者；
- 按顺序排列的章节 Markdown 正文。

旧项目始终只读，不会被修改。旧记忆、人物识别、设定、关系图、AI 结果、缓存、提案、更新状态与回收站均不导入；需要时请在新项目中从正文重新提取。

## 测试与打包

```powershell
python -m unittest discover -s tests
.\scripts\build_windows.ps1 -PythonExecutable python
```

Windows 包为独立的 PyInstaller one-folder ZIP，不包含 Electron、Node.js、Sidecar 或旧自动更新器。详见 `PACKAGING.md`。

## 数据与网络边界

项目与设置默认保存在本地。只有用户主动运行 AI 任务时，任务需要的正文和上下文才可能通过本机 `dsh` 发往其配置的服务。应用不提供旧版本自动更新或跨系列更新通道。

许可证与第三方说明见 `LICENSE`、`PRIVACY.md`、`THIRD_PARTY_NOTICES.md` 和 `licenses/`。
