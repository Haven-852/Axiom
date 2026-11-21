# Agent 设计

## software-agent-sdk：创建、注册、执行

创建：`Agent` 继承 `CriticMixin`、`ResponseDispatchMixin`、`AgentBase`（`openhands-sdk/openhands/sdk/agent/agent.py`）。文档字符串给出的构造是 `Agent(llm=llm, tools=[Tool(name="TerminalTool"), Tool(name="FileEditorTool")])`。`AgentBase` 在 `agent/base.py`，字段包括系统提示模板、工具表、condenser。另一条实现是 `ACPAgent`（`agent/acp_agent.py`），`agent_kind()` 返回 `"acp"`，用来桥接 Agent Client Protocol，而不是再写一套工具循环。

注册：

- 工具：`openhands-sdk/openhands/sdk/tool/registry.py` 的 `register_tool(name, factory)`。`factory` 只能是带 executor 的 `ToolDefinition` 实例，或实现了 `create` 的子类。全局字典 `_REG` 用 `RLock` 保护。`resolve_tool` 按 `Tool.name` 取出 resolver。
- Agent：`openhands-sdk/openhands/sdk/subagent/registry.py` 的 `register_agent(name, factory_func, description)`，工厂签名是 `Callable[[LLM], Agent]`，存在 `_agent_factories`。

执行：`LocalConversation.run`（`conversation/impl/local_conversation.py`）在锁内循环调用 `self.agent.step`。`Agent.step` 包一层观测后进入 `_step`。`_step` 先执行尚未匹配的 pending actions（确认通过后的动作），再调用 `prepare_llm_messages(state.view, condenser=self.condenser, llm=self.llm)`。若返回 `Condensation`，只发压缩事件并返回。否则采样模型，由 `ResponseDispatchMixin._handle_tool_calls` 分发。工具批次交给 `ParallelToolExecutor.execute_batch`（`agent/parallel_executor.py`）。默认 `tool_concurrency_limit` 决定线程池大小，实例注释写明嵌套子 Agent 使用自己的池和 `ResourceLockManager`，避免和父级死锁。

上下文：

- 事件日志是 `ConversationState`（`conversation/state.py`）。`append_event`、`active_branch`、`view` 维护当前分支。`ConversationExecutionStatus` 含 `idle`、`running`、`paused`、`waiting_for_confirmation`、`finished`、`error`、`stuck`、`deleting`。
- 压缩器在 `context/condenser/`：`CondenserBase`、`LLMSummarizingCondenser`、`NoOpCondenser`、`PipelineCondenser`。`AgentBase.supports_condenser` 用来区分 ACP Agent（ACP 返回 False，见 `acp_agent.py` 的 `supports_condenser`）。
- 卡死检测是 `StuckDetector.is_stuck`（`conversation/stuck_detector.py`），覆盖重复动作、重复错误、独白、交替模式、上下文窗口错误。`LocalConversation._run` 在循环里调用 `_check_stuck_or_nudge`。

工具调用契约：`tool/schema.py` 的 `Action` / `Observation` 都是 Pydantic 模型。`tool/tool.py` 的 `ToolExecutor` 是泛型抽象类，`ToolDefinition` 把 schema 和 executor 绑在一起。内置工具包括 `ThinkTool`、`FinishTool`。具体研发工具在 `openhands-tools`：`TerminalTool`、`FileEditorTool`、`TaskTool`、`TaskTrackerTool`、`StartChildConversationTool`、`AskUserTool`，以及一组 `project` 工具（`ListWorkersTool`、`MessageWorkerTool` 等）。

异常：`_step` 路径上有 `_emit_tool_error`。运行循环的文档字符串写明：迭代上限或 stuck 不抛出，把 `execution_status` 设为 `ERROR` 或 `STUCK`；本地 `run` 在 LLM 错误时抛 `ConversationRunError`，`RemoteConversation.run` 也会抛 `ConversationRunError`。`StuckDetector` 还能生成 `get_action_error_nudge`，把错误塞回模型而不是直接失败。

## Agent Canvas

Canvas 不创建 Agent 对象。它用 TypeScript client 连接 agent-server。确认按钮在 `src/components/shared/buttons/conversation-confirmation-buttons.tsx`，变更函数是 `useRespondToConfirmation`。Agent 的创建、上下文和工具执行发生在 SDK / agent-server。

## automation

没有 Agent 类。一次自动化对应数据库里的 `Automation` 和 `AutomationRun`。执行阶段通过 `utils/agent_server.py` 与 `utils/sandbox.py` 找到 agent-server。Agent 的上下文仍是 agent-server 的会话文件。

## MetaGPT

创建：`Role`（`metagpt/roles/role.py`）持有 `actions`、`RoleContext`、`Planner`。`RoleContext` 里有 `memory`、`working_memory`、`msg_buffer`、`todo`、`react_mode`。`Team.hire`（`metagpt/team.py`）把角色放进 `Environment`。

注册：没有 SDK 那种全局工具表。动作是 `Action` 子类列表，环境 API 用 `mark_as_readable` / `mark_as_writeable` 登记到 `ReadAPIRegistry` / `WriteAPIRegistry`（`environment/base_env.py`）。

执行：`Role.run` 先 `_observe`，再 `react`。`react` 按 `RoleReactMode` 分发：`REACT` 和 `BY_ORDER` 走 `_react`（`_think` 与 `_act` 交替，上限 `max_react_loop`）；`PLAN_AND_ACT` 走 `_plan_and_act`，调用 `planner.update_plan` 后按 `current_task` 执行。`_act_on_task` 在基类里 `raise NotImplementedError`。

上下文：`Memory`（`metagpt/memory/memory.py`）存在角色上。`Team.serialize` 把 `model_dump()` 写到 `team.json`。这是文件快照，不是事件溯源。

异常：预算耗尽使用 `NoMoneyException`（`team.py` 的 import）。角色循环本身没有 SDK 那种确认状态机。

## mini-swe-agent

创建：`agents/__init__.py` 的 `get_agent(model, env, config)` 按配置选类。默认类是 `agents/default.py` 的 `DefaultAgent`，构造参数收进 `AgentConfig`（`system_template`、`instance_template`、`step_limit`、`cost_limit`、`wall_time_limit_seconds`）。

注册：没有工具注册表。模型输出的 `extra["actions"]` 是命令字典，`execute_actions` 对每个 action 调用 `self.env.execute(action)`。

执行：`DefaultAgent.run` 写入 system 和 user 消息后 `while True: self.step()`。`step` 是 `execute_actions(self.query())`。`query` 在步数、花费、墙钟超限时抛 `LimitsExceeded` 或 `TimeExceeded`。`FormatError` 会计入 `n_consecutive_format_errors`，达到 `max_consecutive_format_errors` 时写入 `RepeatedFormatError` 并结束。其他异常进 `handle_uncaught_exception`，把 traceback 写入一条 `role="exit"` 的消息后重新抛出。每步 `finally` 调用 `save`。

上下文：就是 `self.messages: list[dict]`。没有 condenser 类。结束条件是最后一条消息 `role == "exit"`。提交协议在环境里：`LocalEnvironment._check_finished` 看到首行 `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 且退出码为 0 时抛 `Submitted`。

## aider

创建：`aider/coders/base_coder.py` 的 `Coder.create` 按 `edit_format` 选择子类（同目录的 `editblock_coder.py`、`udiff_coder.py`、`wholefile_coder.py`、`architect_coder.py` 等）。`ArchitectCoder` 继承 `AskCoder`，覆写 `reply_completed`。

执行：`Coder.run` 在无预置消息时循环 `get_input` → `run_one`。`run_one` 调 `send_message`，若有 `reflected_message` 则继续，最多 `max_reflections`（类属性默认 3）。这是单会话补丁循环，不是多工具 Agent。

上下文：聊天块由 `format_chat_chunks` / `format_messages` 组装，仓库地图来自 `RepoMap`（`aider/repomap.py`，用 tree-sitter）。`check_tokens` 处理窗口。历史摘要走 `summarize_worker`。

异常与修复：`lint_edited` 调用 `self.linter.lint`。反射消息把 lint/测试失败喂回模型。`auto_commit` 在 `auto_commits` 且非 `dry_run` 时调用 `GitRepo.commit`。

## cline

创建：`createAgent` / `createAgentRuntime` 返回 `AgentRuntime`（`sdk/packages/agents/src/agent-runtime.ts`）。工具放在 `Map<string, AgentTool>`。

执行：`AgentRuntime.run` 循环模型请求，再 `executeToolCalls`。工具前有 hook（`beforeTool`）和策略：`policy.enabled === false` 直接跳过；`policy.autoApprove === false` 时调用 `requestToolApproval`。没有回调则拒绝执行，原因字符串写明 "no approval callback is configured"。

上下文：`state.messages`。溢出和 max-tokens 有一次性恢复计数（`overflowRecoveryAttempted`、`maxTokensRecoveryCount`）。会话落盘在 core 包的 `UnifiedSessionPersistenceService` 子类，另有 `SqliteSessionStore`。

多 Agent：`AgentTeam` 与 `TeamChildSessionManager` 管理子会话，不是 MetaGPT 那种共享消息黑板。

## 对 ASE 的直接含义

ASE 的 Agent 循环应调用 SDK 的 `Agent.step` / `LocalConversation.run`，不要重写采样和工具分发。ASE 自己的 LangGraph 节点只决定“这一轮要跑哪一段会话、是否暂停等人”。工具扩展继续走 `register_tool`，这样 Go/C/C++ 侧只实现 `Action`/`Observation` 的 JSON 和 `BaseWorkspace` 的命令/文件方法，不必链接 Python Agent。

多角色协作不要引入 MetaGPT 的 `Role` 对象。若需要规划者和实现者，用 SDK 的 `register_agent` 加 `StartChildConversationTool`，由 LangGraph 决定何时启动子会话。
