# Phase 2.0 初始审计

日期：2026-11-23。审计在改 Agent 协议之前完成。Git 上已发布的提交只有 `710105c` 和 `8dcac16`。Phase 1 到 1.2.1 的应用代码仍在工作区，尚未提交。

## 1. Workflow、Execution、Attempt 的职责

Workflow 是一次研发流程。`workflows` 表保存状态、项目、需求和 `thread_id`。LangGraph 用这个 id 做 checkpoint。项目级互斥在 `WorkflowService.start`：PostgreSQL 对项目行 `FOR UPDATE`，已有未结束工作流时返回 409。

Execution 是一次实际执行。`executions` 保存 workspace、conversation、container、operation、attempt 和执行状态。状态机在 `src/ase/execution/__init__.py`。测试、事件和用量都挂在 execution 上。

Attempt 没有单独的表。身份是 `execution_operations.id`，格式为 `{workflow_id}:{task_id}:{kind}:{attempt}`，并有唯一约束 `(workflow_id, task_id, kind, attempt)`。`claim_operation` 遇到已完成的操作会把原记录交回去，调用方跳过重复编码。repair 使用新的 attempt。

## 2. 有没有等价的 Agent 逻辑运行记录

没有。`AgentRunResult` 是 `CodingAgentAdapter.execute_task` 的返回值，包含 session、执行状态、事件和 conversation id。它不是 Workflow 里一次 Agent 逻辑调用的持久记录，也不能表达 Research 这种不产生 Execution 的运行。

`agent_registry` 表只保存静态 `AgentSpec`。`default_agents()` 在启动时写入 research、planning、coding、testing、diagnosis、evaluation 六条规格。没有工厂，没有运行记录。

## 3. 已有抽象

- `AgentSpec`：静态规格，含输入输出 schema、runtime、timeout、retry_limit。没有 version、capabilities、enabled。
- `Task`：计划里的开发任务，不是 Agent 调用。
- LangGraph 节点：`analyze`、`plan`、`validate`、审批、`workspace`、`develop`、`test`、`diagnose`、`repair`、`evaluate`、`deliver`。
- `CodingAgentAdapter`：OpenHands 与 Scripted 的端口。唯一允许导入 `openhands.sdk` 的文件是 `src/ase/adapters/openhands.py`。

## 4. LangGraph 是否直接调用 OpenHands

生产图 `src/ase/workflow/graph.py` 的 `_run_coding` 调用 `ctx.coding.create_session` 和 `execute_task`。`coding_runtime=openhands` 时，这个对象是 `OpenHandsCodingAdapter`。图不导入 OpenHands SDK。恢复、幂等和 `before_final_commit` 都在这个函数里。本阶段不改这条路径。

## 5. 现有执行入口能否给 Coding Agent 用

没有名为 Execution Service 的类。可复用的入口是 `CodingAgentAdapter`、`claim_operation`、`save_execution` 和 `DockerSandbox`。Coding Agent 应调用这些现成函数，不新建容器或会话实现。

## 6. PostgreSQL 事务

`session_scope` 在成功时 commit，异常时 rollback。PostgreSQL 的项目锁和后续的仓库租约都放在同一个 session 里。SQLite 测试通过 `create_all` 建表，不走 Alembic。已有 PostgreSQL 必须走新的 migration。

## 7. 恢复依赖的字段和状态

- Checkpoint：`thread_id = workflow_id`。审批中断和节点续跑依赖它。
- Execution：`conversation_id`、`container_id`、`operation_id`、`attempt`、`status`。
- Operation：`status=completed` 时跳过编码；`conversation_id` 复用原会话。
- TestResult：同一 execution 已有通过记录时，测试节点不再执行 pytest。
- 容器标签 `ase.role=agent` 和 workspace 哈希，用于进程重启后采纳容器。
- 会话文件在 worktree 的 `workspace/conversations`。交付提交排除该目录。

## 8. 直接复用

Workflow、Execution、Operation、Artifact、TestResult、ExecutionEvent、ModelUsage、`session_scope`、状态机写法、`WorkflowConflict`、`redact`、ScriptedCodingAdapter、现有生产图和 OpenHands 适配器。

## 9. 最小调整

- `AgentSpec` 增加 version、capabilities、enabled、max_retries，都有默认值。
- 新增 `agent_runs` 与 `agent_run_executions`。不把 Agent 运行塞进 checkpoint，也不复制 Execution。
- `ModelUsage` 增加可空的 `agent_name`、`agent_run_id`。没有用量时不写行，不补 0。
- `update_workflow` 在进入 delivered、failed、cancelled 时释放该工作流持有的仓库租约。
- `WorkflowService.start` 在项目锁之外再取仓库租约。两个项目指向同一 Git 仓库时返回 409。
- `tests/test_isolation.py` 改为两个独立仓库副本。原先它让两个项目共用一个仓库并同时交付，这和仓库写锁冲突。不同仓库并行、同一仓库冲突，分开验证。
- 生产图里的 OpenHands 调用顺序、conversation 重连和崩溃点保持不动。

## 不采用的做法

不把 `AgentRunResult` 改名为 AgentRun。不新建第二套 Execution。不把六个业务 Agent 做成完整实现。不改 OpenHands 上游。
