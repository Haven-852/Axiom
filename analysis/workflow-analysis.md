# 工作流

ASE 要的工作流是：拆解任务、分配、协作、并行、中断恢复、人工审批。下面按源码说明每个项目实际做到哪一步。

## 任务拆解

| 项目 | 源码中的机制 | 没有的东西 |
| --- | --- | --- |
| SDK | `conversation/goal/controller.py` 的 `GoalController` 在一轮 `run` 结束后判断继续、停止或等待用户。`GoalStatus` 含 `running`、`complete`、`capped`、`interrupted`。工具层另有 `TaskTool` / `TaskTrackerTool`（`openhands-tools/.../task/definition.py`、`task_tracker/definition.py`） | 没有 LangGraph 图。`GoalController` 注释写明它不做 I/O，驱动者负责真正 `run` |
| Canvas | 展示会话与确认，不拆任务 | 无规划器 |
| automation | `Automation` 是触发定义，不是把需求拆成子任务。`scheduler_loop` 只为到期项插入 `AutomationRun` | 无需求分解 |
| MetaGPT | `Role._plan_and_act` 调 `planner.update_plan`，再按 `planner.current_task` 执行。`software_company.startup` 用固定轮数 `n_round` 跑公司 | 计划结构在角色内部，不是可查询的图 |
| mini-swe-agent | 不拆任务。`DefaultAgent.run` 把整段 `task` 放进一条 user 消息 | 无规划器 |
| aider | `ArchitectCoder.reply_completed` 在 architect 格式下先产出方案再编辑。`/ask`、`/architect`、`/code` 是 `Commands.cmd_ask` 等命令切换模式 | 无任务图，无分配 |
| cline | `SqliteAgendaTaskStore` 存任务状态 `pending_approval`、`approved`、`in_progress`。`in_progress` 要求 `approvedRevision === revision` | 任务是清单记录，不是图运行时 |

## 分配与协作

SDK：子 Agent 用 `register_agent` 注册工厂。`StartChildConversationTool` 打开子会话。`conversation/request.py` 有 `StartChildConversationRequest`，测试 `tests/tools/child_conversation/test_start_child_conversation.py` 使用 `"isolation": "worktree"`。项目工具 `ListWorkersTool`、`MessageWorkerTool`、`ReadWorkerTool` 在 `openhands-tools/openhands/tools/project/definition.py`，面向一组 worker 会话，而不是进程内函数调用。

MetaGPT：`Team.hire` 把多个 `Role` 放进同一个 `Environment`。`Environment.publish_message` 按地址把 `Message` 放进角色的 `msg_buffer`。`Environment.run` 对非 idle 角色 `asyncio.gather`。这是轮次内并行，角色之间靠消息，不靠共享任务队列。

cline：`TeamChildSessionManager` 和 `AgentTeam` 管父子会话。与 SDK 子会话同类，和 MetaGPT 黑板不同。

mini-swe-agent、aider、automation、Canvas 都没有可分配的多 Agent 运行时。automation 的“分配”是把 run 交给另一个 dispatcher 进程。

## 并行

- SDK 工具级并行：`ParallelToolExecutor.execute_batch`。这是一批工具调用并发，不是多个需求并行。
- SDK 会话级并行：多个 conversation，每个可有自己的 worktree（见执行引擎文档）。
- MetaGPT：`Environment.run` 里 `asyncio.gather(*futures)`。
- cline：VS Code worktree 功能文案写明用于并行任务，实现是 `createWorktree` 开独立工作树和窗口，不是运行时调度器。
- mini-swe-agent：`execute_actions` 是列表推导顺序调用 `env.execute`。评测批处理在 `run/benchmarks/`，那是跑很多实例，不是一个任务内部并行。
- aider：单线程 `Coder.run` 循环。

## 中断与恢复

SDK 是这七个里最完整的：

- `LocalConversation.pause` 把状态推向 `PAUSED`。`_run` 在持锁检查到 `PAUSED` 或 `STUCK` 时跳出循环。
- `LocalConversation.interrupt` 存在。`CancellationToken` 传给并行执行器。
- 再次 `run` 时，若状态是 `IDLE`、`PAUSED`、`ERROR`、`STUCK`，会转回 `RUNNING`。`_resume_if_stuck` 在开跑前调用。
- 确认暂停：状态 `WAITING_FOR_CONFIRMATION`。下次 `run` 先把该状态转回 `RUNNING`，`Agent._step` 发现 `get_unmatched_actions` 非空就执行这些已确认动作，不再向模型采样。
- 持久化是 `base_state.json` 加事件目录。`event_service.py` 注释写明恢复时 `agent=None`，让 `LocalConversation` 从 `base_state.json` 重建。
- `GoalAsked` 表示目标循环停在 `ask_user`，用户回答后可以再开。

MetaGPT：`Team.serialize` / `deserialize` 读写 `team.json`。CLI `startup` 有参数 `recover_path`。恢复的是序列化角色，不是事件日志，也没有“暂停在某个工具调用上”的状态。

mini-swe-agent：`DefaultAgent.save` 把 `messages` 和 `trajectory_format: "mini-swe-agent-1.1"` 写入 `output_path`。这是轨迹归档。`run` 每次把 `self.messages = []` 重开，源码里没有从轨迹中途恢复进 `run` 的循环。

aider：`Commands.cmd_undo`、`cmd_load`、`cmd_save`。中断是 `Coder.keyboard_interrupt`。恢复依赖 git 历史和聊天存档，不是运行状态机。

cline：`SessionRuntime` 拒绝并发 run（`SessionRunInProgressError`）。会话文件和 SQLite 可以重开。`AgentRuntime` 使用 `AbortController`。这是会话级恢复，不是图节点级 checkpoint。

automation：`AutomationRunStatus` 含 `CANCELLED`。调度循环用 `FOR UPDATE SKIP LOCKED`（`scheduler.py` 注释）让多个调度器不抢同一行。恢复粒度是 run 行，不是 Agent 步。

## 人工审批

SDK：`security/confirmation_policy.py` 三个策略：`AlwaysConfirm.should_confirm` 恒为 True，`NeverConfirm` 恒为 False，`ConfirmRisky` 用 `threshold` 和 `confirm_unknown`。`Agent._analyze_for_confirmation` 在单个 `FinishAction`、`ThinkAction` 或只结束回合的只读工具时跳过确认。否则调用 `state.security_analyzer.analyze_actions`。`LocalConversation.set_confirmation_policy` 和 `reject_pending_actions` 把决定写回会话。Canvas 只负责把这个状态画出来。

mini-swe-agent：`InteractiveAgentConfig.mode` 为 `human`、`confirm`、`yolo`。`confirm` 模式下未命中 `whitelist_actions` 正则的命令要用户确认。这是终端提示，没有风险等级。

cline：`requestToolApproval` 是注入的回调。任务表还有独立的人工批准：`SqliteAgendaTaskStore` 对需要批准的任务写入 `pending_approval`，用户任务可直接 `approved`。两套批准不要混成一个机制。

aider：用户在提示符决定下一条输入；`/run` 由 `Commands.cmd_run` 执行命令。没有 `should_confirm(risk)` 这种策略对象。

MetaGPT：`Role.is_human` 只决定是否使用 `HumanProvider`，不是确认策略。人工确认在另一条路径：`Planner.ask_review`（`metagpt/strategy/planner.py`）在 `auto_run` 为假时调用 `AskReview.run`（`metagpt/actions/di/ask_review.py`），用终端输入 `confirm` / `change` / `exit` 决定任务是否完成。`auto_run` 为真时不再询问，只看 `task_result.is_success`。`RoleZero.ask_human` 仅在环境是 `MGXEnv` 时调用 `MGXEnv.ask_human`，否则返回 “Not in MGXEnv”。这是终端问答，没有风险等级，也没有“暂停在某个工具调用上”的状态。

automation：没有动作级审批。

## ASE 应采用的工作流形状

LangGraph 图放在 ASE 进程里，节点边界对齐 SDK 已经有的状态，而不是另造一套 Agent 步：

1. `plan` 节点：一次短 `LocalConversation`，或只调用规划用 Agent。
2. `implement` 节点：在该任务的 git worktree 里 `run`，确认策略用 `ConfirmRisky`。
3. 图的 `interrupt`：当 SDK 状态变成 `WAITING_FOR_CONFIRMATION` 或 `GoalAsked` 时挂起，把 `action id` 写入 PostgreSQL。Web 或 Cursor 回复后，再 `run` 一次，让 `_step` 执行 pending actions。
4. `verify` 节点：跑测试命令。失败则回到 `implement`，次数封顶后进入 `stuck`，对应 SDK 的 `STUCK` / `ERROR`。
5. 并行只发生在两个地方：图上互不依赖的节点，以及节点内部的 `ParallelToolExecutor`。不要再套一层 MetaGPT `gather`。

automation 的 `PENDING → RUNNING → COMPLETED/FAILED/CANCELLED` 用作 **图运行** 的数据库状态，不用作 Agent 步状态。Agent 步仍以 SDK 事件日志为准。
