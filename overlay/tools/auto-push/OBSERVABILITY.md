# Auto-Push 可观测性与验收约定

## 关键步骤事件

正式运行中的关键步骤统一写入 `events.jsonl`，`type` 为 `step`。每条记录必须包含：

- `name`：稳定步骤名，例如 `preflight`、`formal-battle`、`settlement-capture`、`evidence-decision`。
- `preconditions`：动作开始前已经确认的条件。
- `action`：本步骤实际执行的动作。
- `postconditions`：动作完成后重新确认的状态。
- `timeout_seconds`：该步骤允许等待的时长；即时事件使用 `0`。
- `evidence`：截图、回调、版本、识别结果或本地校验数据。
- `failure`：成功时为 `null`，失败时保存可直接定位问题的原因。

步骤记录只描述已经发生的事实，不能用预期值填充 `postconditions`。例如任务完成回调只能记录为 `verification_required=true`，不能提前记录 `result_verified=true`。

## 结算证据

Combat 结果按以下顺序采集和裁决：

1. MaaCore 识别到三星结算回调时，在回调返回前抓取结算截图并立即运行本地识别。
2. 截图和 Core 回调分别保存，不互相覆盖。
3. 截图与 Core 一致时，才标明两个证据源一致。
4. 截图二星、非三星或未知，而 Core 报告三星时，结果记为 `conflict`，运行阶段保持 `UNKNOWN`，不能进入 `SUCCEEDED`。
5. 没有明确的正式结算截图时，Core 三星事件本身不能证明通关。

任务资源名 `StageDrops-Stars-3` 和 `Copilot@StageDrops-Stars-3` 由同一个规范化解析器处理。

## 识别验收集

真实截图和回调样本必须放在源码仓之外。`recognition-check` 读取外部清单，不接受模板拼图替代人工确认样本。

清单格式：

```json
{
  "schema_version": "localai.recognition-corpus/v1",
  "reviewed_by": "reviewer",
  "reviewed_at": "2026-11-16T12:00:00+08:00",
  "cases": [
    {
      "id": "unique-case-id",
      "image": "captures/unique-case.png",
      "source": "real_screenshot",
      "human_confirmed": true,
      "expected_visual": "three_star",
      "expected_callback_three_star": true,
      "callbacks": []
    }
  ]
}
```

调用入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools\localai-workflow\localai.ps1 debug recognition-check `
  --corpus "D:\path\to\real-corpus\manifest.json" `
  --report "D:\path\to\reports\recognition-check.json"
```

报告分别统计误判为三星、漏判三星、视觉标签冲突、回调冲突和证据裁决冲突。没有真实语料运行结果时，识别器只能标记为 `UNVERIFIED`。

## 故障回放

`debug replay` 会重新执行：

- 已保存结算截图的本地识别；
- 已保存 MaaCore 回调的任务状态轨迹；
- 截图与 Core 回调的证据裁决。

回放不会伪造设备状态，也不会重新发送点击。需要观察真实页面跳转、停止和恢复时，仍要在隔离设备做一次实机复测。

## 持久化队列

连续推图以 `campaign_id` 为根标识，状态保存在
`<evidence-root>/_campaigns/<campaign_id>/state.json`，事件保存在同目录的
`events.jsonl`。状态写入使用原子替换，事件只追加。

队列只在明确的二星或非三星正式结果后切换下一候选。三星后进入下一关，直到
所有关卡完成。任务错误、断连、未知结果、证据冲突、关卡绑定错误、尝试次数、
时间和理智预算任一不满足时，队列暂停或阻塞并保存检查点。

恢复暂停队列必须提供新的恢复证据：

```powershell
... debug run-campaign `
  --campaign-id "campaign-id" `
  --resume-campaign `
  --recovery-evidence "state-memory/home" `
  ...
```

`campaign-status` 可只读查看当前关卡、尝试、预算和队列状态，不会控制 ADB 或游戏。

## 全自动验收

`全自动已实现` 必须同时满足：

- 程序独立完成导航、编队、正式开战、三星判断、结果核验、失败恢复和下一关决策；
- Codex 只旁观并通过 `run_id`、`attempt_id`、步骤事件和证据包收集结果；
- 未知结果先暂停并核实现场，不自动重复消耗理智；
- 冲突证据保留双份并进入待核验状态；
- 全程未使用理智药或源石；
- 至少有一次真实 Debug 执行链路证据。

需要 Codex 点击、判断或恢复的步骤，必须记录为尚未实现的自动化能力。
