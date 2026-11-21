# 技术选型与二次开发方案

## 决定

ASE 的基础项目是 **OpenHands/software-agent-sdk**（分析提交 `885242b563b1d2695d6c7b3d4bfe642f1c8838a3`，包版本 1.63.1，MIT）。

使用方式：ASE 作为模块化单体，**依赖** `openhands-sdk`、`openhands-tools`、`openhands-workspace`，在进程内调用 `Agent` 与 `LocalConversation`。不 fork 七个上游仓库，不把 Agent Canvas、automation、MetaGPT、mini-swe-agent、aider、cline 放进同一个代码树。

这个选择同时满足已经定下的约束：

| 约束 | SDK 中的现状 | ASE 要补的部分 |
| --- | --- | --- |
| 模块化单体 | SDK 是库；agent-server 是另一个 FastAPI 应用 | ASE 只建一个 FastAPI 应用，把 SDK 当库 |
| FastAPI | `create_app` 在 agent-server | ASE 自己的路由。需要兼容时再反代 agent-server，不合并源码 |
| LangGraph | 七个仓库都没有 | ASE 新建 `workflow` 包 |
| Pydantic V2 | `pydantic>=2.14.3` | 直接沿用，图状态也用 V2 模型 |
| PostgreSQL | agent-server 的 pyproject 声明了 SQLAlchemy，源码未使用；会话在 `base_state.json` | ASE 自建表。运行状态学 automation 的 `AutomationRun` |
| Docker | `DockerWorkspace` | 不可信代码走这条实现 |
| Git Worktree | `_create_conversation_worktree` 在 agent-server | ASE 的 workspace 模块发出同样的 `git worktree add`，把得到的目录交给 `LocalWorkspace` |
| Cursor + Web | ACP 在 `ACPAgent`；Web 产品是另一个仓库的 Canvas | Cursor 走 ASE HTTP 或 ACP；Web 第一期只做 ASE 自己的审批与任务页 |
| 以后的 Go/C/C++ | `RemoteWorkspace.execute_command` 已是 HTTP | 执行器协议冻结为命令与文件两类 JSON |

不选其他项目当基础的原因：

- Agent Canvas 是 `@openhands/agent-canvas`，仓库里已经没有 Python Agent。
- automation 没有 Agent 循环，它通过 `find_agent_server_url` 把活交给 SDK 服务。
- MetaGPT 的 `Team` / `Role` 与 PostgreSQL、worktree、FastAPI、Docker 沙箱都不在一条架构上。
- mini-swe-agent 的 `DefaultAgent.run` 每次清空 `messages`，没有确认状态机。
- aider 的 `Coder.run` 绑在终端 `get_input` 上。
- cline 的 `AgentRuntime` 是另一套 TypeScript 循环，和 SDK 并存会导致两套工具和两套审批。

## 目标进程

```text
Cursor 插件 / Web
        |  HTTPS JSON（任务、批准、事件流）
        v
ASE FastAPI 单体
  api          路由与鉴权
  workflow     LangGraph：plan → implement → verify → review
  store        PostgreSQL：项目、运行、批准、事件索引
  runtime      适配 openhands-sdk
  executor     LocalWorkspace | DockerWorkspace | RemoteWorkspace
        |
        v
git worktree（每个 run 一个目录）
```

Agent 步的事实来源仍然是 SDK 的事件日志和 `base_state.json`。PostgreSQL 只保存 ASE 需要查询的索引：run 状态、worktree 路径、待批准的 action id、conversation id。不把 `ConversationState` 改写成 ORM。agent-server 自己也是这个边界：`event_service.py` 写明 `base_state.json` 是 Agent 的单一事实来源。

## 模块边界

`api`
FastAPI 路由。Web 与 Cursor 共用。批准请求体只带 conversation id 与 action id，对应 `LocalConversation.reject_pending_actions` 或再次 `run`（确认后的 pending actions 由 `Agent._step` 执行）。

`workflow`
LangGraph `StateGraph`。节点调用 `runtime`，不调用模型 SDK。图状态使用 Pydantic V2。人工节点使用 LangGraph interrupt，触发条件是 SDK 状态 `WAITING_FOR_CONFIRMATION`，或 `GoalController` 返回 `GoalAsked`。不要在图里重写 `Agent.step`。

`store`
SQLAlchemy 2 异步会话 + Alembic。状态枚举对齐 `AutomationRunStatus`：`PENDING`、`RUNNING`、`COMPLETED`、`FAILED`、`CANCELLED`。再加 `WAITING_FOR_APPROVAL`，因为 automation 没有动作级确认，这一态是 ASE 的。迁移启动方式可参考 automation `lifespan` 里的 `command.upgrade`，URL 转换参考 `normalize_url_for_alembic`。

`runtime`
构造 `LLM`、`Agent`、`LocalConversation`。工具用 `register_tool` 注册。默认确认策略 `ConfirmRisky`，分析器至少挂 `PatternSecurityAnalyzer`。子任务用 `register_agent` 与 `StartChildConversationTool`，隔离参数使用已有的 worktree isolation。目标是否做完交给 `GoalController`，图节点只看 `GoalOutcome.status`。

`executor`
对上是一个小 Protocol：`execute`、`read_file`、`write_file`。对下：

- 本机可信仓库：`LocalWorkspace`
- 默认不可信仓库：`DockerWorkspace`
- Go/C/C++：命令和文件走与 `RemoteWorkspace` 相同的 HTTP。工具若不想写 Python `ToolExecutor`，用 MCP：`MCPServer`（`openhands-sdk/openhands/sdk/mcp/config.py`）的传输是 `stdio`、`http`、`streamable-http` 或 `sse`。`ToolExecutor` 本身是 Python 抽象类，不是跨语言 ABI

Worktree 在调用执行器之前创建。命令序列与 `conversation_service._create_conversation_worktree` 保持一致：`git worktree add -b <prefix><run_id> <path> <start>`。删除时 `git worktree prune`，分支保留，与 `remove_conversation_worktree` 相同。执行器只接收目录，不知道 LangGraph。

## 分阶段

阶段 1，骨架
一个可启动的 FastAPI 应用，PostgreSQL 能插入 run，LangGraph 只有一个节点：对临时目录跑通 `LocalConversation.run`。测试用 pytest 覆盖“创建 run、跑完、状态变为 COMPLETED”。不接 Docker。

阶段 2，执行隔离
加上 worktree 创建与 `DockerWorkspace`。同一组执行器契约测试先打 Python 实现。确认策略打开后，危险命令必须停在 `WAITING_FOR_APPROVAL`，再次调用才执行。这一步对应 `Agent._step` 里“有 pending actions 就先执行”的行为，测试要断言不会在等待期间再次请求模型。

阶段 3，双入口
Web 只做任务列表、日志和批准。Cursor 侧发同样的 HTTP；若编辑器要流式工具协议，再接 `ACPAgent`，不把 cline 的 VS Code 扩展嵌进来。

阶段 4，其他语言执行器
用 mini-swe-agent `Environment.execute` 的返回形状做契约测试：`output`、`returncode`、`exception_info`。Go 实现通过后，`RemoteWorkspace` 指向该进程。Agent、图、数据库都不变。

明确不做：

- 不合并 MetaGPT 的 `software_company` 流程。
- 不把 aider 的 `Coder` 设为默认编辑器。仓库地图若以后需要，做成一只 `ToolDefinition`。
- 不把 automation 的用户、组织和 webhook 模型搬进第一期表结构。
- 不修改上述七个上游仓库。

## 工程化对照里仍要自建的部分

日志：SDK 使用自己的 `get_logger`。ASE 在 FastAPI 中间件记录 run id，不改 SDK 日志格式。automation 的 `TelemetryContextMiddleware` 只说明“中间件注入上下文”这种做法可行。

观测：agent-server 的 `telemetry/` 是可选 PostHog。ASE 第一期用结构化日志即可。不要把 PostHog 当依赖。

部署：第一期是单个容器跑 ASE API，PostgreSQL 另容器，Docker socket 只在需要 `DockerWorkspace` 时挂载。不要同时部署 Canvas、automation、agent-server 三套，除非阶段 3 选择“Web 直接用官方 Canvas”。那是可选部署，不是代码合并。

质量：SDK 上游测试不作为 ASE 的 CI 全量。ASE CI 跑自己的图、数据库和执行器契约。评测脚本可以另跑 mini-swe-agent 的 `swebench` 入口，结果不写进在线事务库。
