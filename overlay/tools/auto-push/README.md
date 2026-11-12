# MAA Auto-Push Core

本目录是《明日方舟》自动推图助手的本地执行与规则规划核心。

当前里程碑不逐帧调用模型，也不通过 Computer Use 执行常规操作。它使用：

- MAA 本地关卡与地图数据；
- OpenCV 做截图变化检测和模板识别；
- ADB 做可重复的截图、点击和滑动；
- 规则引擎分析路线、阻挡点和部署候选；
- 本地 Copilot 作业库和账号干员数据进行兼容性评分；
- 本地模型只在经过规则校验的候选策略中选择高层方案。

## 设计边界

- 模型不接收游戏截图，也不输出逐帧点击。
- 常规操作由 ADB/MAA 执行。
- Computer Use 只用于首次探索、关键节点验证和异常调试。
- 记录 Computer Use 次数；截图次数、工具调用次数、模型 Token 和策略试跑次数仍按轮设置硬限制。
- Computer Use 不设全局硬上限，但常规操作必须固化为 ADB/MAA 本地脚本，尽量减少对 Codex 在线交互的依赖。
- 所有测试结果写入 JSON，不因 Token 预算跳过必要验证。
- 模型不能编造干员数据或关卡事实；干员兼容性由本地规则评分。
- OpenCV 固定为 2 个线程，ADB 截图默认为 1.5 秒节流并复用最近画面。
- MaaCore 自动化进程以 Below Normal 优先级运行。
- 本地模型使用 2048 上下文、64 批大小和 15 秒保活，减少显存常驻。

## 目录

- `autopush/map_planner.py`：根据 MAA 地图数据寻找路线、交汇点和部署候选。
- `autopush/strategy.py`：扫描本地 Copilot JSON，并按账号练度评分。
- `autopush/adb.py`：ADB 截图与输入，所有操作计入预算。
- `autopush/recognizer.py`：OpenCV 帧变化门限与模板匹配。
- `autopush/model_client.py`：仅处理文本的高层本地模型客户端。
- `autopush/planner.py`：组合地图、策略和可选模型选择。
- `autopush/maa_core.py`：通过 MaaCore DLL 直接执行 Copilot 文件并接收回调。
- `autopush/verifier.py`：使用 MAA 结算模板识别三星、非三星和未知画面。
- `autopush/trial.py`：默认真实作战，可选演习模式，并限制失败与 Computer Use 次数。
- `autopush/state_memory.py`：用截图哈希、缩略图和颜色直方图识别探索过的页面。
- `autopush/navigation.py`：仅在稳定且白名单页面内发送 ADB 点击，过渡画面一律等待。
- `autopush/trial_executor.py`：串联准备页面、Copilot 执行、截图验证和有限 Computer Use 回退。
- `autopush/cli.py`：离线规划与 ADB 检查入口。
- `autopush/evidence.py`：运行 ID、原子状态、增量事件和停止请求。
- `autopush/debug_cli.py`：状态、快照、正式单关、停止和回放的 JSON 调试入口。
- `autopush/states.py`：准备、执行、验证、成功、失败、停止、暂停、未知和预算耗尽。
- `autopush/device_control.py`：与 WPF 共用的跨进程设备独占控制。

## 回调与状态

Auto-Push 按 MaaCore 当前数值协议解析回调，不依赖任务链回调中不存在的
`what` 字段：

- `10000 TaskChainError`
- `10001 TaskChainStart`
- `10002 TaskChainCompleted`
- `10003 TaskChainExtraInfo`
- `10004 TaskChainStopped`
- `3 AllTasksCompleted`
- `2 ConnectionInfo`，其中 `Disconnect`、`ScreencapFailed`、
  `ResolutionChanged` 等终止链路按断连或失败处理。

任务链回调必须读取 `taskid` 和 `taskchain`。`TaskChainCompleted` 只进入
`VERIFYING`；只有正式作战后的结算画面被验证为三星，运行阶段才进入
`SUCCEEDED`。停止、断连、错误、未知和预算耗尽分别保留终止阶段。

## 运行

依赖已固定存放在 D 盘的虚拟环境：

```powershell
D:\work\maa-deps\python-autopush\Scripts\python.exe -m unittest discover `
  -s D:\work\MaaAssistantArknights\tools\auto-push\tests
```

分析 TL-8：

```powershell
$env:PYTHONPATH = "D:\work\MaaAssistantArknights\tools\auto-push"
D:\work\maa-deps\python-autopush\Scripts\python.exe -m autopush.cli analyze `
  --stage-id act55side_08 `
  --map "D:\work\MaaAssistantArknights\resource\Arknights-Tile-Pos\act55side_08-activities-act55side-level_act55side_08.json" `
  --roster "D:\work\MaaAssistantArknights\tools\auto-push\config\example-roster.json" `
  --substitutions "D:\work\MaaAssistantArknights\tools\auto-push\config\example-substitutions.json" `
  --strategies "D:\work\MaaAssistantArknights\resource\copilot" "C:\Users\root\Documents\Codex\2026-11-10\ca\outputs" `
  --output "D:\work\CodexOutputs\MAA-LocalAI\data\autopush\tl8-plan.json" `
  --report "D:\work\CodexOutputs\MAA-LocalAI\data\autopush\tl8-report.json"
```

此阶段仍是 dry-run，只生成经过校验的路线和策略选择，不自动发送游戏输入。

## 本地 Copilot 执行

先从作业站按精确关卡下载候选作业，并识别当前账号干员：

```powershell
D:\work\maa-deps\python-autopush\Scripts\python.exe -m autopush.cli fetch-jobs `
  --stage act55side_09 `
  --output-dir "D:\work\CodexOutputs\MAA-LocalAI\data\autopush\jobs\tl9" `
  --report "D:\work\CodexOutputs\MAA-LocalAI\data\autopush\fetch-tl9-report.json"

D:\work\maa-deps\python-autopush\Scripts\python.exe -m autopush.cli recognize-roster `
  --dll "D:\work\CodexOutputs\MAA-LocalAI\MaaCore.dll" `
  --resource-dir "D:\work\CodexOutputs\MAA-LocalAI\resource" `
  --user-dir "D:\work\CodexOutputs\MAA-LocalAI" `
  --adb "D:\Program Files\Netease\MuMu\nx_main\adb.exe" `
  --address "127.0.0.1:16384" `
  --connect-config General `
  --output "D:\work\CodexOutputs\MAA-LocalAI\data\autopush\OperBoxData.json" `
  --report "D:\work\CodexOutputs\MAA-LocalAI\data\autopush\roster-report.json"
```

`fetch-jobs` 只保存 `stage_name` 与目标 `stage_id` 完全一致的公开作业，避免关键词搜索把其它关卡混入候选。

模拟器正常启动且 ADB 可用后，可以直接通过 MaaCore 执行选中的本地作业：

```powershell
D:\work\maa-deps\python-autopush\Scripts\python.exe -m autopush.cli run-copilot `
  --stage act55side_08 `
  --dll "D:\work\CodexOutputs\MAA-LocalAI\MaaCore.dll" `
  --resource-dir "D:\work\CodexOutputs\MAA-LocalAI\resource" `
  --user-dir "D:\work\CodexOutputs\MAA-LocalAI" `
  --adb "D:\Program Files\Netease\MuMu\nx_main\adb.exe" `
  --address emulator-5554 `
  --connect-config General `
  --copilot "C:\Users\root\Documents\Codex\2026-11-10\ca\outputs\TL-8-manual-team.json" `
  --report "D:\work\CodexOutputs\MAA-LocalAI\data\autopush\run-report.json"
```

任务完成回调不能被当作三星通关。只有截图识别或 Computer Use 关键节点验证通过后，试跑才会记为成功；这条边界用于防止把 `TaskChainCompleted` 和实际三星结果混淆。

验证一张结算截图：

```powershell
D:\work\maa-deps\python-autopush\Scripts\python.exe -m autopush.cli verify-result `
  --stage act55side_08 `
  --image "D:\path\to\settlement.png" `
  --template-root "D:\work\MaaAssistantArknights\resource\template\Battle" `
  --report "D:\work\CodexOutputs\MAA-LocalAI\data\autopush\verify-result-report.json"
```

默认试跑顺序为：

1. 使用经过本地校验的候选路线执行正式作战；
2. OpenCV 模板确认正式作战结果；
3. 只有明确验证为三星才把关卡记为首次通关；
4. 非三星则按兼容性评分尝试下一条路线；
5. 画面无法判定时最多使用有限次 Computer Use，超过预算立即停止。

演习不是正式作战的前置条件。仅在用户显式选择调试用演习模式时才进入演习。

页面状态记忆位于：

```text
D:\work\CodexOutputs\MAA-LocalAI\data\autopush\state-memory
```

目前已验证：

- 主页、设置、条款页、信用交易所和卫戍协议终端可离线识别；
- 游戏加载黑屏会被判定为 `unknown`，不会继续发送点击；
- 页面识别不调用本地模型。
- 默认直接进行正式作战并验证结果；显式启用演习时才执行可选演习流程。
- 未知结算画面只有在剩余预算内才允许 Computer Use 回退。
- 点击、滑动和返回会递增动作序号并使旧截图缓存失效；每次截图记录采集时间、
  截图序号和动作序号。
- Python 调试执行器与 WPF 使用相同的设备命名互斥量，不能同时控制同一 ADB 地址。

## 低资源启动

- `D:\work\CodexOutputs\MAA-LocalAI\启动-Ollama-受限.bat`：单模型、单并发、短保活启动 Ollama。
- `D:\work\CodexOutputs\MAA-LocalAI\启动-MAA-LocalAI-低资源.bat`：启动 MAA 并降低进程优先级。
- `D:\work\CodexOutputs\MAA-LocalAI\启动-MuMu-低优先.bat`：启动 MuMu 后降低虚拟机进程优先级。
- MuMu 当前限制为 30 FPS、低性能模式、低 GPU 模式，并启用动态降帧。
