# ASE V1.0 实现规格

状态与 ADR 0001 相同：**提议，等待批准。** 本文件不创建应用代码。

版本锚点：

| 上游 | 提交 | 发行版本 |
| --- | --- | --- |
| OpenHands/software-agent-sdk | `885242b563b1d2695d6c7b3d4bfe642f1c8838a3` | `openhands-sdk` / `openhands-agent-server` / `openhands-tools` / `openhands-workspace` 1.63.1 |
| OpenHands/OpenHands | `7975a21659ee700ec159279ca06a8ad29307a998` | `@openhands/agent-canvas` 1.43.0，`@openhands/typescript-client` 1.63.1 |

## 进程

```text
浏览器
  ASE Web 壳（量化流程页）
  @openhands/agent-canvas（会话、文件、终端）
        | 工作流 JSON                         | 会话 WebSocket / 文件 / bash
        v                                     v
ASE FastAPI 单体                         openhands-agent-server 1.63.1
  LangGraph + PostgreSQL                 Workspace、Tools、Terminal、事件日志
        |
        +---- adapters 是唯一允许 import openhands.sdk 的包
```

Agent Server 由进程管理器按钉死版本启动，不把 `openhands-agent-server` 源码复制进本仓库。ASE 单体不挂载 `create_app` 的全部路由。

## 上游复用映射

方式只有两种：`依赖` 表示 ASE 进程内 import 发布包；`调用` 表示 HTTP/WebSocket，源码留在上游进程。没有 `复制后修改`。

| ASE 需要 | 仓库 | 目录 | 文件 | 接口 | 方式 |
| --- | --- | --- | --- | --- | --- |
| 创建会话 | software-agent-sdk | `openhands-sdk/openhands/sdk/conversation` | `request.py` | `StartConversationRequest` | 依赖，仅 adapter |
| 会话字段 | 同上 | 同上 | `request.py` | `ConversationConfig.workspace`、`worktree`、`worktree_options`、`confirmation_policy`、`security_analyzer` | 依赖 |
| worktree 选项 | 同上 | 同上 | `request.py` | `ConversationWorktreeOptions` | 依赖 |
| 容器限额 | 同上 | 同上 | `request.py` | `ConversationContainerOptions`；`StartConversationRequest.conversation_runtime`、`container` | 依赖 |
| 确认策略 | software-agent-sdk | `openhands-sdk/openhands/sdk/security` | `confirmation_policy.py` | `ConfirmRisky`、`NeverConfirm` | 依赖 |
| 风险分析 | 同上 | 同上 | `defense_in_depth/pattern.py`、`analyzer.py` | `PatternSecurityAnalyzer`、`SecurityAnalyzerBase` | 依赖 |
| 本地目录句柄 | software-agent-sdk | `openhands-sdk/openhands/sdk/workspace` | `local.py` | `LocalWorkspace` | 依赖，只作为请求字段 |
| 远程执行 | software-agent-sdk | `openhands-sdk/openhands/sdk/workspace/remote` | `base.py` | `RemoteWorkspace.execute_command`、`file_upload`、`file_download` | 调用。ASE 不包装第二套 |
| Docker 工作区 | software-agent-sdk | `openhands-workspace/openhands/workspace/docker` | `workspace.py` | `DockerWorkspace` | 留在 Agent Server 进程，ASE 不实例化 |
| 终端工具 | software-agent-sdk | `openhands-tools/openhands/tools/terminal` | `definition.py` | `TerminalTool` | 由 Agent Server 注册，ASE 不注册 |
| 文件编辑 | software-agent-sdk | `openhands-tools/openhands/tools/file_editor` | `definition.py` | `FileEditorTool` | 同上 |
| 子会话 | software-agent-sdk | `openhands-tools/openhands/tools/child_conversation` | `definition.py` | `StartChildConversationTool` | 同上。隔离值 `worktree` 已有测试 |
| 目标循环 | software-agent-sdk | `openhands-sdk/openhands/sdk/conversation/goal` | `controller.py` | `GoalController`、`GoalOutcome`、`GoalAsked` | 调用 Agent Server 的 goal 路由，ASE 不内嵌循环 |
| 启动会话 | software-agent-sdk | `openhands-agent-server/openhands/agent_server` | `conversation_router.py` | `start_conversation`，`POST ""` | 调用 |
| 中断 | 同上 | 同上 | `conversation_router.py` | `interrupt_conversation`，`POST /{conversation_id}/interrupt` | 调用 |
| 真正创建 worktree | 同上 | 同上 | `conversation_service.py` | `_plan_conversation_worktree`、`_create_conversation_worktree` | 只存在于 Agent Server 进程 |
| 删除 worktree | 同上 | 同上 | `local_storage.py` | `remove_conversation_worktree` | 同上 |
| 工具审批 | 同上 | 同上 | `event_router.py`、`event_service.py` | `respond_to_confirmation`，`POST /respond_to_confirmation`；`EventService.respond_to_confirmation` | 调用 |
| 服务进程 | 同上 | 同上 | `api.py` | `create_app` | 独立进程，不在 ASE 里调用 |
| 会话 UI | OpenHands | `src/components/conversation`、`files`、`terminal` | `src/lib/index.ts` | 包导出 `./conversation`、`./files`、`./terminal`；`AgentServerUIRoot`、`AgentServerUIProviders` | npm 依赖 |
| 前端如何表达 worktree | OpenHands | `src/api/conversation-service` | `agent-server-conversation-service.api.ts` | 创建请求里的 `worktree` 布尔 | 参考调用方式，不复制该文件 |
| 确认按钮 | OpenHands | `src/hooks/mutation`、`src/components/shared/buttons` | `use-respond-to-confirmation.ts`、`conversation-confirmation-buttons.tsx` | `useRespondToConfirmation` | 随 Canvas 组件使用，不把这些文件拷进 ASE |

不复用、也不复制：MetaGPT 的 `Team`/`Role`，mini-swe-agent 的 `DefaultAgent`，aider 的 `Coder`，cline 的 `AgentRuntime`，automation 服务本体。automation 的 `AutomationRunStatus` 只作为 ASE 枚举命名参考。

MCP 留给 V1 之后的非 Python 工具：`openhands-sdk/openhands/sdk/mcp/config.py` 的 `MCPServer`，传输 `stdio`、`http`、`streamable-http`、`sse`。V1 量化回测走 Agent Server 已有终端，不新写 Go 执行器。

## ASE 自有模块

批准后的单体只有这些包。它们都不 import `openhands`，除了 `adapters.openhands_sdk`。

| 模块 | 职责 |
| --- | --- |
| `ase.main` | 创建 FastAPI 应用，挂路由和生命周期 |
| `ase.config` | `pydantic-settings`：数据库 URL、Agent Server 基址、会话密钥、worktree 前缀 |
| `ase.contracts` | 模块之间的 Pydantic V2 模型。LangGraph、API、适配器都用这一套 |
| `ase.api` | 项目、run、阶段审批、量化假设 |
| `ase.workflow` | LangGraph 图、节点、检查点 |
| `ase.adapters.agent` | 领域端口 `AgentPort` |
| `ase.adapters.openhands_sdk` | 唯一的 SDK/HTTP 翻译 |
| `ase.store` | SQLAlchemy 模型与仓储 |
| `ase.quant` | 假设、回测结果引用、接受标准。不执行回测命令 |
| `web` | 薄壳。流程页是自有的，会话页嵌入 Canvas |

## Agent Adapter

领域端口，供 LangGraph 节点调用。它不知道 HTTP，也不知道 `StartConversationRequest`。

```text
AgentPort
  open(task: AgentTask) -> AgentSession
  wait(session, until: WaitCondition) -> AgentSnapshot
  interrupt(session) -> None
  resolve_tool_approval(session, decision: ToolDecision) -> None
```

`AgentTask`、`AgentSession`、`AgentSnapshot`、`ToolDecision`、`WaitCondition` 定义在 `ase.contracts.agent`。`WaitCondition` 只有三个值：`finished`、`waiting_for_tool_approval`、`asked_user`。它们对应 Agent Server 的 `ConversationExecutionStatus` 与 goal 的 `GoalAsked`，但枚举属于 ASE。

V1 只有一个实现：`OpenHandsAgentAdapter`，构造时注入 `OpenHandsSdkAdapter`。量化角色不是新的 Agent 类，而是 `AgentTask.profile`：

| profile | 作用 | 传给上游的内容 |
| --- | --- | --- |
| `quant_researcher` | 把假设写成可回测的任务说明 | system prompt 后缀 + 只读工具倾向 |
| `quant_implementer` | 在 worktree 里改代码并运行回测命令 | 默认工具集，确认策略 `ConfirmRisky` |
| `quant_reviewer` | 读回测输出，给出是否建议接受 | 不授予会改主仓库的说明；工作目录仍是该 run 的 worktree |

角色注册若需要 `register_agent`，只发生在 Agent Server 侧的配置，不在 ASE 进程里维护一份并行注册表。

## OpenHands SDK Adapter

`OpenHandsSdkAdapter` 是防腐层。

输入：`AgentTask`。输出：`AgentSnapshot`。内部可以 import：

- `StartConversationRequest`、`ConversationWorktreeOptions`、`ConversationContainerOptions`
- `LocalWorkspace`
- `ConfirmRisky`、`PatternSecurityAnalyzer`

它做四件事：

1. `POST /api/conversations`，处理函数是 `conversation_router.start_conversation`。请求体由 `StartConversationRequest` 序列化。`worktree=True`。`conversation_runtime` 在量化 run 上取 Agent Server 已声明可用的 docker runtime。`container` 填写内存和 CPU。`confirmation_policy` 使用 `ConfirmRisky`。
2. 轮询会话状态，映射到 `WaitCondition`。不把 SDK 的 `ConversationState` 泄漏给 workflow。
3. `resolve_tool_approval` 调用 `event_router.respond_to_confirmation`。
4. `interrupt` 调用 `interrupt_conversation`。

失败时抛 ASE 的 `AgentUnavailable` 或 `AgentRejected`。禁止把 `httpx` 的异常冒泡到 LangGraph 节点。

浏览器上的文件树和终端不经过这个适配器。Canvas 用 `@openhands/typescript-client` 直连 Agent Server。ASE 只把 `conversation_id` 和短期会话地址交给壳。

## LangGraph 工作流

图名 `quant_research`。状态模型 `QuantRunState`（`ase.contracts.workflow`）。检查点使用 `langgraph-checkpoint-postgres`，表与业务表分库或分 schema，避免把对话事件写进检查点。

节点顺序：

1. `intake`：校验假设与数据范围，写 `research_projects` / `experiment_runs`。
2. `plan`：`AgentPort.open(profile=quant_researcher)`，直到 `finished`。
3. `implement`：`profile=quant_implementer`。若快照是 `waiting_for_tool_approval`，图停在工具审批；批准后再次 `wait`。这一停靠 Agent Server 的 pending action，不是 LangGraph 的阶段门。
4. `backtest`：仍在同一会话里要求运行约定的回测命令。命令本身由 Agent Server 的 `TerminalTool` 执行。ASE 只记录退出约定：成功则产出 artifact URI。
5. `review`：`profile=quant_reviewer` 生成接受建议。
6. `stage_gate`：`interrupt`。人在 ASE API 提交阶段决定之前，图不得进入 `publish`。
7. `publish`：把 run 标为完成，并调用 Agent Server 删除会话以触发 `remove_conversation_worktree`。分支按上游行为保留。

边：`review` 判定回测失败且重试次数未到上限时回到 `implement`。上限写在 `QuantRunState.attempt_limit`，默认 3。

图节点禁止调用模型 SDK，禁止 `subprocess`，禁止 `git`。

## 数据库模型

SQLAlchemy 2 声明式模型，Alembic 迁移。不建会话事件表。

`research_projects`

- `id` UUID 主键
- `name`
- `repo_url`
- `base_ref`
- `created_at`

`experiment_runs`

- `id` UUID 主键
- `project_id` 外键
- `hypothesis`
- `status`：`pending`、`running`、`waiting_for_tool_approval`、`waiting_for_stage_approval`、`completed`、`failed`、`cancelled`
- `graph_thread_id`
- `attempt`
- `created_at`、`updated_at`

`conversation_bindings`

- `run_id` 外键，唯一
- `conversation_id` UUID，Agent Server 返回值
- `agent_server_base_url`
- `workspace_path` 可空，来自会话信息
- `runtime` 文本，例如 docker

`approvals`

- `id` UUID
- `run_id`
- `layer`：`tool` 或 `stage`
- `subject_id`：工具动作用 action id，阶段用节点名 `stage_gate`
- `decision`：`pending`、`approved`、`rejected`
- `comment`
- `decided_at`

`artifacts`

- `id` UUID
- `run_id`
- `kind`：`plan`、`backtest`、`review`
- `uri` 指向 worktree 内或对象存储中的文件
- `created_at`

索引：`experiment_runs(project_id, status)`，`approvals(run_id, layer, decision)`。

## API

前缀 `/api/v1`。请求和响应都是 `ase.contracts.api` 里的 Pydantic 模型。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `POST` | `/projects` | 创建 `research_projects` |
| `GET` | `/projects/{project_id}` | 读取项目 |
| `POST` | `/projects/{project_id}/runs` | 写入假设并启动图。返回 `run_id` |
| `GET` | `/runs/{run_id}` | 运行状态、绑定的 `conversation_id`、当前等待原因 |
| `POST` | `/runs/{run_id}/tool-approvals` | 转发到 `respond_to_confirmation` |
| `POST` | `/runs/{run_id}/stage-approvals` | 恢复 LangGraph `stage_gate` |
| `POST` | `/runs/{run_id}/interrupt` | 调 `interrupt_conversation`，run 置 `cancelled` 或保持可恢复 |
| `GET` | `/runs/{run_id}/artifacts` | 列出 artifact URI |

不提供 ASE 版的 `/bash`、`/file`、`/git`。这些继续是 Agent Server 的路由：`bash_router`、`file_router`、`git_router`。

Web 壳拿到 `GET /runs/{id}` 里的 `conversation_id` 后，把 Canvas 指到 Agent Server。阶段按钮调用 ASE 的 `stage-approvals`。工具确认按钮留在 Canvas 已有的确认 UI。

## Git Worktree

ASE 不执行 git。

`OpenHandsSdkAdapter.open` 设置：

- `worktree=True`
- `worktree_options=ConversationWorktreeOptions(base="ref", ref=<project.base_ref>, branch_prefix="ase/")`

Agent Server 在 `conversation_service._create_conversation_worktree` 中执行 `git worktree add -b <branch> <path> <start>`。默认根目录行为以 `ConversationConfig.worktree` 字段说明为准。删除走会话删除后的 `remove_conversation_worktree`，分支保留。

量化仓库必须已经是 git 仓库。非 git 目录时上游 `_plan_conversation_worktree` 返回空，适配器把这次 open 视为失败，图进入 `failed`。禁止回退到在主工作区直接改文件。

## Docker 沙箱

量化 `implement` 与 `backtest` 必须设置 `StartConversationRequest.conversation_runtime` 为服务端列出的 docker runtime，并带 `ConversationContainerOptions` 的内存、CPU。浏览器开关用该模型的 `browser` 字段，量化任务设为关闭。

`DockerWorkspace` 与 `docker_runtime` 路由留在 Agent Server。ASE 的验收检查只看绑定记录里的 `runtime` 是否为 docker，以及回测命令没有在 ASE 进程内执行。

可信的只读规划可以仍用 docker。V1 不为了省事提供「在 ASE 宿主机跑回测」的开关。

## 人工审批

两层，不能并成一个接口。

工具层：Agent Server 状态 `waiting_for_confirmation`。策略对象是 `ConfirmRisky`。前端使用 Canvas 已挂上的 `useRespondToConfirmation`。ASE 的 `POST /runs/{id}/tool-approvals` 是给 Cursor 或无 UI 调用方的同一动作，最终仍到达 `respond_to_confirmation`。拒绝使用该接口的拒绝语义，不在 ASE 里删除 worktree。

阶段层：只有 `stage_gate`。LangGraph `interrupt` 把 run 写成 `waiting_for_stage_approval`，并插入 `approvals.layer=stage`。`approved` 才进入 `publish`。`rejected` 进入 `failed`，不合并到主分支。

`AskUser` 若出现，映射为 `WaitCondition.asked_user`，图停住，答案仍由 Canvas 送回 Agent Server。这不是阶段门。

## 许可证与版本

- ASE 自有代码使用 MIT，与 OpenHands 的 MIT 兼容。
- 不把上游源码树并入本仓库，因此不需要在发行物里再分发一份 OpenHands 源码。依赖声明必须保留上游许可证。
- `NOTICE` 记录 SDK 1.63.1、agent-canvas 1.43.0、typescript-client 1.63.1 及对应提交。
- Python 用 `uv.lock` 精确锁定。Node 用 lockfile 精确锁定 `@openhands/agent-canvas` 与 `@openhands/typescript-client`。两者必须来自同一对提交，禁止只升其中一个。
- 升级上游是新的 ADR，不是普通依赖更新。
- Apache-2.0 的 aider 与 cline 不进入依赖。

## 批准后才新增的文件

现有分析文档和本规格之外，批准前一个应用文件都不建。

| 路径 | 作用 |
| --- | --- |
| `pyproject.toml` | ASE 包；依赖 FastAPI、LangGraph、langgraph-checkpoint-postgres、SQLAlchemy、Alembic、Pydantic V2、httpx、openhands-sdk==1.63.1 |
| `uv.lock` | 锁定 |
| `alembic.ini` | 迁移配置 |
| `alembic/env.py` | 迁移环境 |
| `alembic/versions/0001_quant_control_plane.py` | 上表五张表 |
| `src/ase/__init__.py` | 包 |
| `src/ase/main.py` | FastAPI 入口 |
| `src/ase/config.py` | 设置 |
| `src/ase/contracts/agent.py` | `AgentTask`、`AgentPort` 用的模型 |
| `src/ase/contracts/workflow.py` | `QuantRunState` |
| `src/ase/contracts/api.py` | 路由模型 |
| `src/ase/api/routes/projects.py` | 项目路由 |
| `src/ase/api/routes/runs.py` | run、中断、artifact |
| `src/ase/api/routes/approvals.py` | 工具审批与阶段审批 |
| `src/ase/workflow/graph.py` | `quant_research` 图 |
| `src/ase/workflow/nodes.py` | 节点，只调 `AgentPort` 和仓储 |
| `src/ase/adapters/agent.py` | `AgentPort`、`OpenHandsAgentAdapter` |
| `src/ase/adapters/openhands_sdk.py` | SDK 防腐层 |
| `src/ase/store/models.py` | ORM |
| `src/ase/store/repository.py` | 查询 |
| `src/ase/quant/acceptance.py` | 回测是否可进入阶段门的纯函数 |
| `tests/test_workflow.py` | 图在工具等待和阶段门处停下 |
| `tests/test_sdk_adapter.py` | 请求体包含 `worktree=True` 与 docker runtime，使用假 HTTP |
| `web/package.json` | 依赖 `@openhands/agent-canvas@1.43.0` |
| `web/src/main.tsx` | 壳 |
| `web/src/pages/RunPage.tsx` | 阶段按钮 + `AgentServerUIRoot` |
| `NOTICE` | 上游许可证与版本 |

不修改的路径：`E:\demo\axiom-src` 下任一上游克隆，以及 OpenHands、software-agent-sdk 的任何远程分支。

## 量化研究验收

批准并实现后，用一个研究假设验收，而不是用生产下单验收。

通过标准：

1. `POST /projects/{id}/runs` 创建一个 run。
2. Agent Server 会话的工作目录是 worktree，不是研究仓库的主工作区。
3. 回测命令发生在 docker runtime 内。
4. 危险命令会停在工具审批；未批准时 run 保持 `waiting_for_tool_approval`。
5. 回测结束后图停在 `waiting_for_stage_approval`。拒绝后主分支没有新的合并提交。
6. 接受后 `artifacts` 至少有一条 `kind=backtest`，run 变为 `completed`。
