# 工程结构

提交锚点与 [repository-comparison.md](repository-comparison.md) 相同。路径相对于各仓库根目录。

## 1. software-agent-sdk

顶层是一个多包仓库，不是单个 Python 包：

- `openhands-sdk/`：Agent、会话、工具协议、安全、本地与远程 Workspace
- `openhands-tools/`：终端、文件编辑、任务、子会话等具体工具
- `openhands-workspace/`：Docker、Apptainer、cloud、remote API 等 Workspace 实现
- `openhands-agent-server/`：FastAPI 服务
- `clients/`、`examples/`、`tests/`

SDK 依赖在 `openhands-sdk/pyproject.toml`：`pydantic>=2.14.3`、`litellm`、`fastmcp`、`websockets`。没有 FastAPI，也没有 SQLAlchemy。FastAPI 在 `openhands-agent-server/pyproject.toml`，同时声明了 `sqlalchemy>=2.1.8` 和 `alembic>=1.20.1`。在 `openhands-agent-server` 的 Python 源码里搜索 `from sqlalchemy` / `import sqlalchemy` / `from alembic`，结果为零。会话真相在文件：`ConversationState._save_base_state` 把快照写入 `BASE_STATE`（调用处称为 `base_state.json`），见 `openhands-sdk/openhands/sdk/conversation/state.py`。

入口：

- 库入口是构造 `Agent` 再构造 `LocalConversation`。`Agent` 在 `openhands-sdk/openhands/sdk/agent/agent.py`。
- HTTP 入口是 `openhands-agent-server/openhands/agent_server/api.py` 的 `create_app`。它调用 `_add_api_routes`，挂上 conversation、file、bash、git、vscode、skills、sub-agents、mcp、projects、docker runtime 等路由。模块末尾有 `api = create_app()`。
- 配置类型是 `openhands-agent-server/openhands/agent_server/config.py` 的 `Config`。`load_config` 让 `OH_*` 环境变量覆盖文件。密钥相关变量包括 `OH_SECRET_KEY`、`OH_SESSION_API_KEYS_0`。

模块依赖方向：agent-server 依赖 sdk 和 tools；tools 依赖 sdk；workspace 包依赖 sdk 的 `RemoteWorkspace`。`DockerWorkspace` 定义在 `openhands-workspace/openhands/workspace/docker/workspace.py`，继承 `RemoteWorkspace`。

## 2. OpenHands（Agent Canvas）

`package.json` 的 `name` 是 `@openhands/agent-canvas`，版本 1.43.0。依赖 `@openhands/typescript-client` 1.63.1，与 SDK 版本 1.63.1 对齐。仓库顶层是 `src/`、`electron/`、`docker/`、`helm/`、`playwright.*.config.ts`、`vite.config.ts`。源码树里没有 Python Agent 包。

脚本：`desktop` 执行 `electron electron`，`build:desktop` 走 `electron-builder`。这是 Web（Vite / React Router）加 Electron 桌面，不是 Cursor 插件。

前端通过 agent-server 类型和 websocket 工作，例如 `src/utils/websocket-url.ts` 注释写明代理路径 `/runtime/{port}/`，`src/hooks/mutation/use-respond-to-confirmation.ts` 把确认回传服务端。

## 3. automation

Python 服务，入口 `openhands/automation/app.py` 的 `_create_app`，标题 "OpenHands Automations Service"，源码里的版本字符串是 `1.34.1`。`lifespan` 里调用 Alembic `command.upgrade`。路由按功能拆开后 `include_router`：uploads、webhook、factory、service、telemetry、usage、git_sync、kv，以及主 `router`。

数据模型在 `openhands/automation/models.py`，SQLAlchemy `DeclarativeBase`。表包括 `automations`、`automation_runs`（类 `Automation`、`AutomationRun`）。`AutomationRunStatus` 有 `PENDING`、`RUNNING`、`COMPLETED`、`FAILED`、`CANCELLED`、`SKIPPED`。若干索引带 `postgresql_where`，说明目标库是 PostgreSQL；`db.py` 的 `normalize_url_for_alembic` 把 `postgresql+asyncpg` 转成 Alembic 用的同步驱动。

它不包含 Agent 循环。`openhands/automation/utils/sandbox.py` 写明本地模式走 `utils/agent_server.py`，`find_agent_server_url` 从 sandbox 字典取 URL。调度在 `scheduler.py` 的 `scheduler_loop`：轮询到期自动化，插入 `PENDING` run，注释写明由另一个 dispatcher 进程取走执行。

配置是 `pydantic-settings`（`pyproject.toml` 依赖 `pydantic-settings>=2`、`fastapi>=0.121`、`sqlalchemy[asyncio]>=2`）。

## 4. MetaGPT

核心在 `metagpt/`。入口 CLI 是 `metagpt/software_company.py` 的 Typer 命令 `startup`，它调用 `generate_repo`，再 `asyncio.run(company.run(...))`。配置类是 `metagpt/config2.py` 的 `Config`，继承 `CLIParams` 和 `YamlModel`。`startup` 里的默认 YAML 模板指向 `config2.yaml` 的 `llm.api_type` / `api_key`。

依赖 `requirements.txt` 钉住 `pydantic>=2.5.3`、`openai`、`typer`、`loguru`、`faiss_cpu`、`qdrant-client` 等。已读的 `requirements.txt` 没有 FastAPI、LangGraph、SQLAlchemy。

角色、团队、环境、动作分属 `metagpt/roles/role.py`、`metagpt/team.py`、`metagpt/environment/base_env.py`、`metagpt/actions/action.py`。

## 5. mini-swe-agent

包在 `src/minisweagent/`。协议在 `src/minisweagent/__init__.py`：`Model`、`Environment`、`Agent`。实现分别在 `models/`、`environments/`、`agents/`。CLI 入口在 `src/minisweagent/__main__.py` 与 `run/mini.py`。配置用 YAML 加 Pydantic，`get_config_from_spec` 在 `config/__init__.py`。全局 dotenv 路径由 `MSWEA_GLOBAL_CONFIG_DIR` 或 `user_config_dir("mini-swe-agent")` 决定。

`pyproject.toml` 依赖 `pydantic >= 2.0`、`litellm`、`jinja2`、`pyyaml`。没有 FastAPI、SQLAlchemy、LangGraph。

## 6. aider

证据来自 GitHub `main` @ `5dc9490bb35f9729ef2c95d00a19ccd30c26339c` 的原始文件。包目录 `aider/`。控制台脚本在 `pyproject.toml`：`aider = "aider.main:main"`。`aider/main.py` 的 `main()` 组装 git、模型、`Coder.create`。

子模块：`coders/`（`base_coder.py` 的 `Coder` 以及 editblock、udiff、wholefile、architect 等格式）、`repo.py` 的 `GitRepo`、`repomap.py` 的 `RepoMap`、`commands.py`、`run_cmd.py`、`io.py`、`linter.py`。

`requirements.txt` 钉住 `pydantic==2.12.5`、`litellm==1.82.3`、`gitpython==3.1.46`、`fastapi==0.135.1`。在已下载的 `aider/main.py`、`coders/base_coder.py`、`commands.py`、`repo.py`、`io.py` 中没有 `FastAPI` 或 `APIRouter`。`fastapi` 是依赖锁定，不是本仓库的服务入口。`docker/Dockerfile` 从 `python:3.12-slim-bookworm` 做应用镜像，安装 git，并把 `HOME` 指到 `/app`。

## 7. cline

npm workspaces：`sdk/packages/*`、`apps/*`。根 `package.json` 的 `build` 先编 SDK，再编 `@cline/cli`。脚本还有 `cli`（`apps/cli`）和 `code`（`@cline/code`）。测试脚本覆盖 `@cline/agents`、`@cline/core`、`@cline/cli`、`@cline/vscode`。

和 ASE 相关的源码位置：

- Agent 循环：`sdk/packages/agents/src/agent-runtime.ts` 的 `AgentRuntime`，工厂 `createAgent`
- 会话与持久化：`sdk/packages/core/src/session/`，含 `SqliteSessionStore`、`FileSessionService`
- 任务审批状态：`sdk/packages/core/src/tasks/store/sqlite-task-store.ts` 的 `SqliteAgendaTaskStore`
- VS Code 扩展：`apps/vscode/`，worktree 工具在 `apps/vscode/src/utils/git-worktree.ts`
- 多 Agent：`sdk/packages/core/src/extensions/tools/team/multi-agent.ts` 的 `AgentTeam`

没有 Python 包，也没有 LangGraph。

## 依赖关系（ASE 视角）

```text
Cursor / Web
    -> ASE FastAPI（自建，LangGraph + PostgreSQL）
        -> openhands-sdk Agent / LocalConversation
            -> ToolDefinition（openhands-tools 或自建）
                -> BaseWorkspace.execute_command / file_*
                    -> LocalWorkspace | DockerWorkspace | 未来的 Go/C/C++ RemoteWorkspace
```

Agent Canvas、automation、cline 都坐在“入口或调度”层，不替代上面的内核。MetaGPT、mini-swe-agent、aider 不进入这条依赖链，只提供对照。
