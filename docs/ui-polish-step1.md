# UI 细化第一步：行为基线与行距技术验证

日期：2026-10-30。基线提交：`d90f700`。

本文记录最初的隔离原型验证；该实现现已转为正式组件，接入与迁移情况见[第四步排版设置](ui-prose-typography.md)。

## 结论与范围

已验证一条可继续接入的纯显示行距路径：保留 `QPlainTextEdit`，使用 `QPlainTextDocumentLayout` 子类调整视觉行的位置和块边界，不修改 `QTextBlockFormat` 或正文。

本步只增加隔离原型、验证脚本、测试及本文档。正式应用没有导入原型，当前字号、行距、设置、保存反馈和行宽行为保持原状；`docs/feature-status.json` 不新增“行距设置已支持”的声明。本步不生成安装包。

## 基线

- 原有全量测试：136 个模块，1165 项测试，10 项按既有条件跳过，0 失败。
- `main.py --self-test` 通过。
- 正文仍使用 `QPlainTextEdit`；配置默认正文字号仍为 16px；既有用户偏好尚未迁移。
- 现有保存修订检查、AI 来源检查、明确确认与取消流程不作修改。

基线在新增测试文件前启动，运行模块列表固定为上述 136 个；新增原型测试单独运行，不将两次运行表述为一次全量回归。

## 排除的实现方式

在当前固定的 PySide6 6.8.3 上，使用 `QTextCursor.mergeBlockFormat()` 设置 180% 段落行高，实际显示的块高度未变，却使文档进入 modified 状态并增加撤销步骤。新增负向对照测试固定了这个观察。

因此不采用段落格式注入，也不通过关闭撤销、清空历史、重载正文或重置 modified 标记来掩盖副作用。

## 原型实现

- `scripts/prototypes/prose_spacing.py`：实验性编辑器与布局子类，仅在创建空编辑器时安装一次布局。
- 复用 Qt 原有的换行、文字绘制和输入交互；在公开的 `blockBoundingRect()` 扩展点同步视觉行的位置及高度。
- 调整行距不替换文档或输入控件，不更改字符／段落格式，不发出正文内容变更信号。
- 保持 Qt 的按需排版；仅重新定位已完成排版的块，其余块访问时再处理。设置变化仍会遍历块，极端大量段落的性能需随正式接入继续关注。
- 保留当前滚动位置，测试以可见文字锚点为准；Qt 懒排版可能重新计算滚动条数字，不能把初次请求的数值当成视口位置。
- 输入法存在 preedit 时延迟行距变更，提交或取消组合输入后再应用，不主动提交／取消用户输入。
- 原型中 1.8 表示自然行进高度的 1.8 倍，范围 1.0–2.4；这是验证用定义，不构成默认配置迁移或最终设置范围的发布。

Qt 公开 API 依据：[QPlainTextDocumentLayout](https://doc.qt.io/qt-6.8/qplaintextdocumentlayout.html)、[QTextLine::setPosition](https://doc.qt.io/qt-6.8/qtextline.html#setPosition)。固定版本的原生排版行为另核对了 [Qt 6.8.3 源码](https://github.com/qt/qtbase/blob/v6.8.3/src/widgets/widgets/qplaintextedit.cpp)。

## 验证结果

新增 `tests/test_prose_spacing_prototype.py`，14 项测试通过，覆盖：

- 干净与未保存文档的正文、修订号、选区、撤销／重做、修改标记及信号。
- 换行后的实际行距、鼠标命中、键盘上下移动、正文增删和撤销后的重新排版。
- 长文滚动锚点、末行可达、查找选区和高亮。
- 明暗主题、变窄窗口、放大字号；空文档、空段落、非 BMP 字符、组合字符及混合方向文字的内容保留。
- 合成 `QInputMethodEvent` 的 preedit、提交与取消。该检查不等于真实 Windows 输入法候选窗验收。
- 在临时项目的真实 `Editor` 中，Markdown 序列化、已加载文件修订、dirty 状态和项目文件字节保持不变。
- 真实 `AIContextSnapshot` 在仅改变行距后继续匹配，实际编辑正文后正常判定过期。

截图验证器 `scripts/validate_prose_spacing.py` 仅创建临时项目，使用测试替换将原型放入真实章节编辑页面；它不修改用户项目，也不调用模型。

矩阵包含浅／深色主题、1100／1440／1600px 窗口、18／24px 正文字号、125%／150%／200% 缩放，共 36 组。检查无横向溢出、点击定位、正文／修订／历史／文件与 AI 快照保护；同时断言实际截图背景颜色，防止只改主题名称而未切换配色。另生成相同条件下 1.0／1.8 行距的对照截图。

本机单次观测：20.4 万字符单段落调整并处理界面事件约 18–20ms；9.6 万字符、多空段落样本约 23–25ms。此数值只用于记录本次原型，不是跨机器性能承诺。

本地截图、JSON 结果和日志在 `output/ui-implementation-polish-step1-20261030/`，受忽略规则保护，不进入源码提交或发行输入。

## 后续接入流程

技术原型通过当前自动化门槛，可按原计划进入保存反馈统一，再实施可拖动行宽；字号／行距设置接入时再将原型收敛为正式组件，并补齐设置保存／放弃、旧偏好迁移和整窗验收。

正式接入前仍需验证真实 Windows 中文输入法候选窗、跨控件切换焦点、拖选、滚轮／触控板及超长组合输入。正式设置应只作用于正文显示，不能修改 AI 候选来源或使布局偏好进入项目 Markdown。

本步不提前实施后续三个功能提交，不更改默认字号、不发布行距设置，也不构建安装包。

## 复现

在仓库根目录执行：

```powershell
.venv\Scripts\python.exe scripts\run_tests.py --pattern test_prose_spacing_prototype.py
$env:QT_SCALE_FACTOR = '1.25' # 另以 1.5、2 分别启动新进程
.venv\Scripts\python.exe scripts\validate_prose_spacing.py --output output\ui-implementation-polish-step1-20261030
```
