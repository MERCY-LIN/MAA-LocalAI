# MAA-LocalAI 仓库约束

本仓库以 `overlay/` 保存 MAA 扩展源码，基线提交记录在 `BASE_COMMIT.txt`。开始工作前必须读取本文件、根 `README.md`、`overlay/AGENTS.md` 和
`overlay/tools/localai-workflow/SKILL.md`。

## 目标与执行边界

- 端到端目标是自动编队、正式作战、三星监控、主动止损、结果验证和连续推图。
- 演习不是正式作战的前置条件。
- 确认三星不可达后必须主动停止并安全退出战斗；无法确认时标记 `BLOCKED` 或 `UNVERIFIED`。
- 优先复用 MAA Core、Copilot 作业和已有模块，不重写已有上游能力。
- 模型只处理高层决策；确定性操作由规则引擎、MAA 和 ADB 完成。
- 默认不使用理智药和源石。正式开战现场无法确认时不得重复开战。

## 结果语义

- `UNKNOWN`、`PAUSED`、`SKIPPED`、`ABORTED`、任务停止和 Core 完成均不得算作三星成功。
- `TaskChainCompleted` 只表示任务链结束，必须用正式作战结算画面验证三星。
- 连续推图只有在结果已验证、资源消耗符合预期且现场可恢复时才可继续。

## 修改与提交

- 开发和故障修复在 `debug` 分支进行；本地测试通过后推送并合并到 `main`。
- 不得用 Mock、接口实现或文档完成代替真实执行链路验证。
- 每次修改必须报告调用入口、修改文件、测试证据和未验证内容。
- 无法满足的要求必须标记 `BLOCKED` 或 `UNVERIFIED`，不得宣称完成。
- 不得在 overlay 中加入 `config/`、`data/`、`debug/`、`cache/`、模型、截图、账户数据或日志。
