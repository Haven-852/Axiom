# Phase 2.4 初始审计

日期：2026-11-23。审计对象是 Phase 2.3 完成时的 Agent、Task、Plan、Approval、Execution 和生产编码图。本文件先于编码任务接入。

## 1. 已有编码执行能力

生产编码图在 `src/ase/workflow/graph.py`。它保留 analyze、plan、validate、approval、workspace、develop、test、diagnose、repair、evaluate、deliver。`develop` 通过现有适配器执行，并使用 `execution_operations` 做 attempt 幂等。Conversation id 存在 Execution 和 Operation 上。OpenHands SDK 只在 `src/ase/adapters/openhands.py` 导入。

`ExecutionService.run_claimed` 已经会：

- 按 `{workflow_id}:{task_id}:{kind}:{attempt}` 领取 operation。
- 已完成的 operation 直接复用 Execution，不再次调用适配器。
- 通过 `agent_run_executions` 关联 AgentRun。
- 把 conversation id 写回 operation。

`CodingAgent` 版本 `1` 已经注册名为 `coding`，但只接收 `instruction`、`workspace_path`、`task_id`，不检查 Plan 是否批准。Phase 2.0 的测试依赖这条旧入口。

Agent Runner 用 PostgreSQL 上的 `idempotency_key` 唯一约束复用已结束的 AgentRun。ModelUsage 在 Runner 里记录。

Repository Lock 使用 `repository_leases` 和 PostgreSQL advisory lock。

## 2. 可以直接复用的节点

生产图里的 test、diagnose、repair、evaluate、deliver 不删除，也不改执行路径。新的已批准任务入口不编译进这条图，避免 `planned` 被解释成开始编码。

Execution、Attempt、Conversation 恢复继续走 `ExecutionService` 和 OpenHands Adapter。不新建 Execution 表、Attempt 表或 Conversation 表。

## 3. Task 与 Plan Version

每个 Plan 版本有自己的 `plan_id`。任务行的 `plan_id` 指向该版本。旧版本任务保持原状态，不被改写。

任务表没有 `plan_version` 列。不需要 Migration：版本 Artifact 用 `plan_id` 找到当前版本，任务用同一个 `plan_id` 归属该版本。不属于当前版本的任务视为被替代，不把历史行改成新状态。

现有状态 `pending`、`ready`、`in_progress`、`blocked`、`completed`、`failed`、`cancelled` 够用。`in_progress` 表示已被领取。`running` 不另增枚举。`superseded` 由“不是当前版本”推导，避免覆盖历史状态。

## 4. Execution 幂等边界

幂等键是 operation id，加上 AgentRun 的 `idempotency_key`。相同任务和 attempt 不会新建 Execution。已完成 operation 不重复执行。进程内没有第二套缓存。

领取任务和资格检查必须放在同一个数据库事务里，对 Workflow 行加锁。SQLite 会忽略 `FOR UPDATE`，并发测试放在 PostgreSQL。

## 5. 本阶段要新增的能力

- Execution Eligibility Gate：执行前再次核对当前版本、批准、hash、澄清、依赖、任务状态和仓库锁。
- 单任务 Scheduler：只用本地拓扑序，优先级只在同一依赖层排序。
- CodingInput 和 Coding Context：结构化目标、证据、验收标准和用户决策。
- CodingAgent 在收到计划字段时先过门禁，再调用 `run_claimed`。
- Task Completion Gate：没有真实 TestResult 和通过的 Evaluation 时，不把任务标成 completed。
- 独立 LangGraph 线程 `{workflow_id}:coding:{task_id}`。它不使用生产图的 thread id。

## 6. Approval Gate

批准记录的 subject 已是 `plan-review:{plan_artifact_id}:{plan_hash}`。门禁读取这条记录，并重新计算 Plan Hash。未批准、旧版本、hash 不一致、阻塞澄清未解决，都在调用 CodingAgent 之前拒绝。

calculator Workflow `1dd7c84d-36d1-4dcb-a66e-1004e0a1985f` 的 Version 2 没有批准。门禁必须拒绝它，包括状态为 ready 的调查和记录任务。

## 7. 已知风险

生产图的旧入口仍然可以在没有 Plan 批准时编码。那是 Phase 1 的路径，本阶段不删除。新的受控入口是唯一接受已批准 Plan 的路径。

Task 完成依赖现有 TestResult 和 Evaluation。CodingAgent 的成功只表示 Execution 结束。

远程 OpenHands 仍由 Adapter 启动容器。本阶段不放宽容器限制。
