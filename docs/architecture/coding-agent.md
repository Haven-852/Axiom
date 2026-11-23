# Coding Agent

名称 `coding`，版本 `1`。它只执行调度器已经选定的任务。

## 输入

`CodingInput` 包含 Workflow、Plan 版本、Artifact、Hash、任务说明、验收标准、相对路径、研究证据、依赖、仓库修订和用户决策。绝对路径和 `..` 会被拒绝。如果给出了仓库修订，而 `git rev-parse HEAD` 已经变化，执行停止。

上下文写明要做什么、不要做什么、验收标准、用户原句和这些约束：不改无关文件，不改 ASE 生产代码，不读密钥，不合并主分支，不降低测试，不自行决定未确认需求。

没有 `plan_id` 的旧调用仍然委托给 `ExecutionService`。那是 Phase 2.0 的适配器复用测试。新的受控入口总会带上计划字段，并在 Agent 内再次执行资格检查。

## 执行链路

显式请求进入独立 LangGraph 线程 `{workflow_id}:coding:{task_id}`。这条线程不使用生产编码图的 thread id，也不会从 `planned` 自动开始。

资格检查通过后领取任务，Agent Runner 调用 CodingAgent，CodingAgent 调用现有 `ExecutionService.run_claimed`。OpenHands SDK 仍然只由 `src/ase/adapters/openhands.py` 导入。Execution 和 AgentRun 通过现有 `agent_run_executions` 关联。Attempt 仍是 `execution_operations`。相同 operation 已完成时复用结果和 Conversation id。

## 完成

AgentRun 成功不等于任务完成。`task_completion.assess` 要求 Execution 完成、TestResult 全部通过，并且 Workflow 的 Evaluation 通过。缺少测试记录时状态是 `unverified`，任务保持 `in_progress`。测试失败不把任务标成完成，生产图里的 diagnose 和 repair 节点仍然保留。

## 安全

容器限制、只读策略和适配器约束没有放宽。受控入口不提供绕过门禁的接口，也不接受任意宿主机路径。工作区必须位于 `workspace_root` 内，并且不能覆盖 ASE 源码目录。
