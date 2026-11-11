# MAA LocalAI + Auto-Push Progress

这是一个面向 MAA `dev-v2` 的本地 AI 作战规划与自动推图扩展进度仓库。

当前仓库采用“overlay 补丁仓”形式，只保存新增和修改文件，不复制 MAA
上游数 GB 的资源与二进制。基础提交记录在 `BASE_COMMIT.txt`。

## 已实现

- 本地 OpenAI 兼容模型作战规划，默认只允许 loopback 地址。
- 模型输出经过关卡白名单、去重、数量上限和次数上限校验。
- MAA `OperBox` 真实账号干员识别。
- PRTS Plus 精确关卡作业查询与完整动作下载。
- 真实阵容兼容性排序。
- ADB 截图节流、输入、页面状态记忆和状态门控导航。
- 先演习三星、再执行正式作战的有界试跑调度。
- 每轮记录 Computer Use、截图、工具调用、DeepSeek Token、策略运行和测试结果。
- Python 测试、LocalAI C# 契约测试、WPF 编译检查、VS Code 任务和 GitHub Actions。

## 当前进度

- 已识别 328 名账号干员。
- 已下载并排序 TL-9、TL-EX-1 至 TL-EX-8 的公开作业。
- TL-9 首选作业 `108702` 与账号阵容匹配，但试跑未通过结果验证。
- 运行包中的 `MaaCore.dll` 早于本地演习模式补丁。重建并同步 Core 和任务资源前，
  自动试跑可能退回真实开战路径，因此目前不声明任何关卡已经三星通关。
- 下一阶段是构建带 `exercise=true` 支持的 `MaaCore.dll`，验证演习入口后再继续
  TL-9 和 EX 关卡。

详见 `overlay/tools/auto-push/STATUS.md`。

## 应用方式

先准备 MAA 源码：

```powershell
git clone https://github.com/MaaAssistantArknights/MaaAssistantArknights.git MaaAssistantArknights
git -C MaaAssistantArknights checkout bd8a2439ab0e7036aa460d4a89de8569a707f3a9
```

然后在本仓库根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\apply-overlay.ps1 `
  -TargetRepo "D:\path\to\MaaAssistantArknights"
```

脚本只覆盖 `overlay/` 中列出的文件，不删除目标仓库中的其它内容。

## 验证

应用后运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File tools\localai-workflow\localai.ps1 verify
```

本进度快照提交前已通过：

- Python：23 项测试通过。
- C# LocalAI 契约：8 项测试通过。
- MaaWpfGui Debug 编译：0 warning，0 error。

## 边界

- 常规重复操作使用 ADB 或 MAA，不让模型逐帧分析截图。
- Computer Use 次数不设全局硬上限，但必须计数，并逐步固化为本地自动化。
- `TaskChainCompleted` 不等于三星通关，必须通过结算截图验证。
- 默认不使用理智药或源石。
- 本项目是 MAA 的衍生修改，遵循 AGPL-3.0-only；MAA 原始许可见 `LICENSE`。
