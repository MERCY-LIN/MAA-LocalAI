# MAA-LocalAI

MAA-LocalAI 是基于 MAA `dev-v2` 的 overlay 源码仓，目标链路为：

1. 用户指定关卡列表。
2. 识别账号干员并筛选兼容的 Copilot 作业。
3. 自动完成编队、正式作战和三星结果验证。
4. 只有验证成功且现场状态可恢复时才继续下一关。

仓库不复制 MAA 上游数 GB 的源码、资源和二进制。`overlay/` 只保存本项目的
新增或修改文件，应用基线固定记录在 `BASE_COMMIT.txt`。

## 当前状态

已纳入 Git 的功能：

- 本地 OpenAI 兼容模型规划，默认仅允许 loopback 端点。
- 模型计划经关卡白名单、去重、数量与次数上限校验。
- MAA `OperBox` 账号识别、PRTS Plus 精确关卡作业查询。
- 阵容兼容性排序、ADB 截图与状态门控导航。
- 按数值 MaaCore 消息 ID 解析回调，关联 `taskid` 和 `taskchain`。
- 独立的准备、执行、验证、成功、失败、停止、暂停、未知和预算耗尽阶段。
- 截图序号、采集时间和动作序号；输入动作后强制使用新帧。
- Python 调试执行器与 WPF 共享设备命名互斥量。
- 正式单关默认直接作战、不使用理智药，Core 完成不等于三星。
- JSON 调试入口：`doctor`、`snapshot`、`run-stage`、`status`、`events`、
  `stop`、`replay`。

尚未完成：

- 用户关卡列表到自动编队、作业筛选、连续推图和重启恢复的完整编排。
- 战斗中“三星不可达”的实时判定。
- 经画面验证的安全退出战斗。
- 完整调度回放和 FlaUI 界面验证。

## 应用 overlay

```powershell
git clone https://github.com/MaaAssistantArknights/MaaAssistantArknights.git MaaAssistantArknights
git -C MaaAssistantArknights checkout bd8a2439ab0e7036aa460d4a89de8569a707f3a9

powershell -NoProfile -ExecutionPolicy Bypass -File .\apply-overlay.ps1 `
  -TargetRepo "D:\path\to\MaaAssistantArknights"
```

脚本会校验目标仓库 HEAD 是否与 `BASE_COMMIT.txt` 一致。上游升级完成并确认
兼容后，才可使用 `-AllowDifferentBase`。

## 调试入口

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File tools\localai-workflow\localai.ps1 debug doctor

powershell -NoProfile -ExecutionPolicy Bypass `
  -File tools\localai-workflow\localai.ps1 debug snapshot --stage act55side_08

powershell -NoProfile -ExecutionPolicy Bypass `
  -File tools\localai-workflow\localai.ps1 debug run-stage `
  --stage act55side_08 `
  --copilot "C:\path\to\job.json" `
  --confirm-formal-battle
```

运行证据写入 MAA 运行包的
`data\autopush\evidence\<run_id>\`，不进入源码仓和 Git。

## 分支与验证

- `debug`：调试 CLI、协议适配和故障路径修复。
- `main`：仅在本地验证通过后由 `debug` 合并。
- 修改源码后运行 `tools\localai-workflow\localai.ps1 verify`。
- 涉及 MaaCore、任务资源或真实执行语义时，还必须完成 Debug 构建和实机链路验证。

`TaskChainCompleted` 仅表示任务链结束，不能代替三星结算验证。停止、断连、
跳过和未知结果均不得计为成功。默认不使用理智药和源石。

本项目是 MAA 的衍生修改，遵循 AGPL-3.0-only。
