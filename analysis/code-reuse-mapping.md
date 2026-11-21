# 代码复用映射

分类只有四种：可以直接依赖或拷贝的代码、通过 SDK/API 调用的能力、只值得参考的设计、不适合引进的模块。许可证：OpenHands 三个仓库、MetaGPT、mini-swe-agent 是 MIT；aider 与 cline 是 Apache-2.0。MIT/Apache 允许嵌入，但 ASE 仍应优先依赖发布包，避免 fork 后无法跟随 `1.63.1` 这条线。

## 可以直接依赖的代码

这些是 Python 库，和 ASE 的 FastAPI 单体同进程使用。不要把整个 Git 树复制进 AXIOM。

| 能力 | 符号 | 路径 |
| --- | --- | --- |
| Agent 循环 | `Agent.step`、`LocalConversation.run` | `openhands-sdk/openhands/sdk/agent/agent.py`，`conversation/impl/local_conversation.py` |
| 工具注册 | `register_tool`、`resolve_tool`、`ToolDefinition`、`Action`、`Observation` | `openhands-sdk/openhands/sdk/tool/registry.py`，`tool/tool.py`，`tool/schema.py` |
| 子 Agent 工厂 | `register_agent` | `openhands-sdk/openhands/sdk/subagent/registry.py` |
| 上下文压缩 | `LLMSummarizingCondenser` | `openhands-sdk/openhands/sdk/context/condenser/llm_summarizing_condenser.py` |
| 确认与分析 | `ConfirmRisky`、`SecurityAnalyzerBase` | `openhands-sdk/openhands/sdk/security/` |
| 秘密掩码 | `SecretRegistry` | `openhands-sdk/openhands/sdk/conversation/secret_registry.py` |
| 终端与编辑工具 | `TerminalTool`、`FileEditorTool` | `openhands-tools/openhands/tools/terminal/definition.py`，`file_editor/definition.py` |
| 本地 / 远程 / Docker 执行 | `LocalWorkspace`、`RemoteWorkspace`、`DockerWorkspace` | `openhands-sdk/.../workspace/`，`openhands-workspace/.../docker/workspace.py` |
| 目标循环判定 | `GoalController` | `openhands-sdk/openhands/sdk/conversation/goal/controller.py` |

前提：ASE 使用 Python 3.12，与 `openhands-sdk/pyproject.toml` 的 `requires-python` 一致。Pydantic 使用 V2，已满足。

## 通过 SDK / API 调用，而不是复制源码

| 能力 | 调用方式 | 依据 |
| --- | --- | --- |
| 现成的 HTTP 会话、文件、bash、git、websocket | 运行 `create_app` 或连接其 URL，用 `RemoteConversation` | `agent_server/api.py`，`conversation/impl/remote_conversation.py` |
| 每个会话一个 worktree | 让 agent-server 创建，或 ASE 发出同样的 `git worktree add` | `conversation_service._create_conversation_worktree` |
| Web 控制台 | 部署 Agent Canvas，让它连接 ASE 背后的 agent-server | `OpenHands/package.json` 包名 `@openhands/agent-canvas`，依赖 `@openhands/typescript-client` |
| 定时 / webhook 触发 | 若需要现成调度，调 automation 的 HTTP，而不是把它的表并进 ASE | `automation/app.py`，`scheduler.scheduler_loop` |
| 编辑器协议 | 使用 SDK 里的 `ACPAgent`，让 Cursor 或兼容客户端走 ACP | `agent/acp_agent.py` |
| 评测一个补丁是否解出 issue | 单独跑 mini-swe-agent 的 benchmark 入口 | `src/minisweagent/run/benchmarks/swebench.py` |

ASE 的主路径不应反向依赖 Canvas 或 automation 的内部模块。它们是可选的外围系统。

## 只值得参考的设计

| 设计 | 看哪里 | ASE 里怎么落地 |
| --- | --- | --- |
| Run 状态机 `PENDING/RUNNING/COMPLETED/FAILED/CANCELLED` | `automation/models.py` 的 `AutomationRunStatus` | LangGraph 运行记录的 PostgreSQL 枚举。不复制 automation 的云账号、sandbox、webhook 全套 |
| Alembic 随进程启动升级 | `automation/app.py` 的 `lifespan` 调用 `command.upgrade` | ASE 自己的迁移。注意它把异步 URL 转成同步 URL |
| 多角色一轮并行 | `MetaGPT` `Environment.run` 的 `asyncio.gather` | 仅当 LangGraph 节点需要扇出时借鉴，不引入 `Role` |
| 计划再执行 | `Role._plan_and_act` | 对应图上的 plan 节点，状态存 PG，不存 `planner` 对象 |
| 薄执行协议 | `minisweagent.Environment.execute` | 写成 ASE 的 `Executor` Protocol，Python 实现转调 `BaseWorkspace` |
| 轨迹 JSON | `DefaultAgent.serialize` 的 `trajectory_format` | 评测导出格式可对齐，在线状态仍用 SDK 事件 |
| 仓库地图与补丁格式 | aider `RepoMap`、`Coder.apply_edits` | 以后可作为一只可选工具，不进入 Agent 主循环 |
| 编辑后 lint / test 再反射 | `Coder.lint_edited`、`auto_commit`、`max_reflections` | verify 节点的行为参考。提交仍走显式 git 工具，并受确认策略约束 |
| 审批回调 | cline `AgentRuntime.requestToolApproval` | Cursor 入口实现成这个回调，内部转成 SDK 的确认回复 |
| 任务修订号批准 | `SqliteAgendaTaskStore` 的 `approvedRevision` | 若 ASE 要“计划变更必须再批准”，照这个不变量写到 PostgreSQL |
| IDE worktree 命令 | `apps/vscode/src/utils/git-worktree.ts` `createWorktree` | Cursor 侧只发“为任务开 worktree”的 API，真正执行在 ASE |
| 确认按钮交互 | Canvas `useRespondToConfirmation` | Web 前端的交互参考，组件不移植 |

## 不适合引进的模块

| 模块 | 原因 |
| --- | --- |
| Agent Canvas 整仓 | TypeScript UI，自带 Electron、Vite、Playwright。ASE 的单体是 Python。前端以后单独写，或整体部署官方 Canvas |
| agent-server 全部路由 | `create_app` 挂了会话、插件、市场、遥测、canvas extension、OpenAI 兼容等数十个路由。放进模块化单体会把产品边界交出去。需要的能力通过 SDK 对象调用 |
| agent-server 的 SQLAlchemy/Alembic 依赖名 | `pyproject.toml` 写了这两项，源码中没有 import。不能把它当成 PostgreSQL 层来复用 |
| automation 服务本体 | 绑定 OpenHands Cloud 的用户、工厂、沙箱和 webhook。表设计可参考，服务不要嵌入 |
| MetaGPT `metagpt/` | gymnasium 环境、固定公司流程、`config2.yaml`、向量库依赖。与 FastAPI / PostgreSQL / worktree / LangGraph 都不重合。`_act_on_task` 在基类直接 `NotImplementedError` |
| MetaGPT 的 Android、Minecraft、Werewolf 环境 | 与软件研发执行器无关 |
| mini-swe-agent 作为平台内核 | 没有会话状态机、没有工具 schema、没有确认策略、没有 worktree。`run` 会清空消息 |
| aider 作为平台内核 | 单机 CLI，`Coder.run` 阻塞在 `io.get_input`。`fastapi` 只出现在 `requirements.txt` 锁定里 |
| aider `run_cmd` / `pexpect` | 无沙箱，和 Docker 执行边界冲突 |
| cline `sdk/` 与 `apps/vscode` | 另一套 TypeScript Agent 运行时。和 `openhands-sdk` 同时作为内核会出现两套工具循环和两套审批 |
| 任何仓库里的 LangGraph 代码 | 七个仓库的已检索源码和依赖中都不存在。编排层必须自写 |

## 测试体系对照

| 项目 | 源码中可见的测试 | 不能声称的能力 |
| --- | --- | --- |
| SDK | 仓库根 `tests/`，按 `tests/sdk`、`tests/agent_server`、`tests/tools` 分。worktree、确认、安全 shell 都有针对性测试 | 未看到独立的“质量评分服务”。目标是否完成由 `GoalController` / judge 在运行时做 |
| Canvas | `package.json` 中 Vitest 与多份 Playwright 配置（`playwright.config.ts`、`playwright.mock-llm.config.ts` 等） | 不测 Python Agent 循环 |
| automation | 仓库根 `tests/` | 不测工具执行正确性 |
| MetaGPT | 仓库根 `tests/` | 不是 ASE 的回归集 |
| mini-swe-agent | 仓库根 `tests/`，另有 `run/benchmarks/swebench.py` 作为 SWE-bench 运行器 | benchmark 是评测脚本，不是 CI 里的单元测试替代 |
| aider | `pytest.ini` 的 `testpaths` 为 `tests/basic`、`tests/help`、`tests/browser`、`tests/scrape` | 无服务器集成测试入口 |
| cline | 根脚本 `test`、`test:unit`、`test:e2e`，包内 vitest | 测的是 TS SDK 和扩展 |

ASE 的回归应分成三层：ASE 自己的 LangGraph 与 PostgreSQL 用 pytest；Agent 行为靠锁定 `openhands-sdk` 版本并跑其上游测试的一小部分冒烟；执行器契约用同一组 JSON 用例打 Python 实现和未来的 Go 实现。不要把七个项目的测试套件合成一个。
