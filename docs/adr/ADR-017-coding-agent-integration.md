# ADR-017 Coding Agent 接入

状态：已接受。

已批准的 Plan 通过独立的 LangGraph 线程进入现有 ExecutionService。生产编码图的 develop、test、diagnose、repair、evaluate 和 deliver 节点保持原路径。

执行前由 Eligibility Gate 再次核对当前 Plan 版本、审批、Hash、澄清、依赖和任务状态。`planned` 和 `ready` 都不是执行授权。未批准的 calculator Version 2 不能启动编码。

任务完成以 TestResult 和 Evaluation 为准，不以 AgentRun 成功为准。不新增 Execution、Attempt 或 Approval 表。OpenHands SDK 的导入位置不变。
