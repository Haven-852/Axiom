# Phase 1.2 初始审计

日期：2026-11-23。仓库 `E:\demo\Axiom`，分支 `main`。最近提交是 `710105c` 和 `8dcac16`。Phase 1 与 Phase 1.1 的应用代码、测试和文档都还没有提交。

`git status` 显示已跟踪文件只有 `.gitignore`、`README.md`、`analysis/technology-selection.md` 有改动。未跟踪目录包括 `src/`、`tests/`、`alembic/`、`docs/`、`docker/`、`ase-poc-demo/`、`docker-compose.yml`、`pyproject.toml`、`.env.example`。

没有删除任何现有文件。`.env` 已在 `.gitignore`。`*.sqlite`、`.venv/`、`var/`、`.pytest_cache/` 也已忽略。密钥不在源码里。可追溯的 PoC 记录是 `docs/reports/real-poc.diff`、`docs/reports/real-poc-log.json`、`docs/reports/phase-1.1-acceptance-report.md` 和 `docs/poc/real-agent-execution.md`。

## 工程结构

- `src/ase/contracts`：领域模型。
- `src/ase/db`：SQLAlchemy 模型、会话和 Alembic。
- `src/ase/store.py`：业务读写。
- `src/ase/workflow`：LangGraph 图和 `WorkflowService`。
- `src/ase/adapters/openhands.py`：唯一允许导入 `openhands.sdk` 的模块。
- `src/ase/infra`：worktree、容器、pytest、命令策略。
- `src/ase/api`：FastAPI。
- `tests`：脚本化图测试、TestLLM、真实模型和 Docker 测试。

## 已有持久化

### ASE 业务库

正式表在 `src/ase/db/orm.py`，迁移 `alembic/versions/0001_phase1.py` 使用 `Base.metadata.create_all`。PostgreSQL 16 由 `docker-compose.yml` 映射到 `127.0.0.1:5433`。`tests/test_postgres.py` 在这台库上跑过迁移和一次脚本化审批流程。

真实 OpenHands PoC 使用的是临时 SQLite，不是 PostgreSQL。

SQLite 路径会在 `make_session_factory` 里自动 `create_all`。PostgreSQL 只走 Alembic。引擎没有 `pool_pre_ping`，也没有连接超时。

主键都是字符串 UUID。外键和部分索引已经存在。`workflows.thread_id` 和 `evaluation_reports.workflow_id` 是唯一的。审批、执行和事件没有“同一工作流只许一条”的约束。

### LangGraph Checkpoint

`src/ase/app.py` 的 `_checkpointer` 固定使用 `langgraph-checkpoint-sqlite` 的 `SqliteSaver`，文件是 `workspace_root` 的上一级 `checkpoints.sqlite`。导入失败时退回进程内 `MemorySaver`。

已安装版本：LangGraph `1.2.24`，`langgraph-checkpoint` `4.3.2`，`langgraph-checkpoint-sqlite` `3.1.3`。`langgraph-checkpoint-postgres` 尚未安装。

`workflow_checkpoints` 表只是节点索引，不是恢复数据本身。

同一进程里的审批暂停和恢复已经由 Phase 1 测试覆盖。跨进程恢复只在脚本化运行时用过同一个 SQLite 文件，没有在真实模型进程被杀死之后验证。

### OpenHands Conversation

对照已安装的 `openhands-sdk==1.63.1` 和上游 `openhands-agent-server`：

- `RemoteConversation` 禁止设置 `persistence_dir`。会话存在 Agent Server 上。
- 传入已有 `conversation_id` 时，客户端先 `GET /api/conversations/{id}`。404 才创建。这是重新连接的官方入口。
- Agent Server 把会话写在 `conversations_path`，默认相对路径 `workspace/conversations`。容器工作目录是 `/workspace`，所以文件实际落在挂载的 worktree 里的 `workspace/conversations/<id>/meta.json`。Phase 1.1 的修复测试日志里已经出现这个目录。
- 服务启动时只加载这份元数据。同一个目录还在时，新的 Agent Server 进程可以重新看到会话。容器被删除且没有另外的持久卷时，目录随 worktree 保留，但新容器必须重新挂载同一 worktree。
- `run()` 把执行循环放在服务端。ASE 进程断开后，只要容器还在，服务端循环可以继续。正在飞的模型 HTTP 请求会随服务端进程消失而中断，不存在跨进程的 exactly-once。
- `execution_status` 在重新 `GET` 会话后从服务端状态恢复。
- 当前适配器每次都新建会话，不保存 `conversation_id`。`WorkflowService._invoke` 的 `finally` 会 `stop_all()`。进程被强制结束后这个清理不一定执行，容器可能留下，但 ASE 没有采纳它的逻辑。

因此不能把 Conversation 文件、LangGraph checkpoint 和业务表当成同一份状态。

## Agent Server 与容器

`AgentContainerManager` 使用 `docker run -d --rm`。限制包括内存 2g、1 CPU、pids 128、`CapDrop=ALL`、`no-new-privileges`、只绑定 `127.0.0.1`、只挂载任务 worktree。inspect 已验证这些限制。没有 `--privileged`，没有 Docker socket。

根文件系统可写。镜像用户是 `openhands`。pytest 若缺失，会在容器里 `pip install`，这要求根文件系统或 site-packages 可写。

容器记录只在进程内存里。没有 Docker label，新进程无法按工作区找回容器和会话密钥。会话密钥目前只放在内存，不写业务库。

## 安全配置

远端工具策略是 `PatternSecurityAnalyzer` 加 `ConfirmRisky(threshold=MEDIUM)`。HIGH 和未知动作会被 `reject_pending_actions` 拒绝，不是 `NeverConfirm`。计划审批仍然是人工门。`merge_to_main` 固定拒绝。

命令策略目前只拒绝少量危险片段，没有 L1/L2/L3 分级。Terminal 工具仍对编码代理开放，Phase 1.1 的成功路径使用过它。

## 测试覆盖

已有真实环境证据：

- `tests/test_llm_config.py`：真实 chat completion。
- `tests/test_real_poc.py`：真实编码并交付。
- `tests/test_real_repair.py`：真实失败后的一次诊断和修复。
- `tests/test_docker_agent.py`：容器 inspect、文件挂载、容器内 pytest、RemoteWorkspace。
- `tests/test_postgres.py`：PostgreSQL 上的脚本化 API 流程。
- `tests/test_openhands_sdk.py`：TestLLM，不是外部模型。

尚未覆盖：PostgreSQL 上的真实模型任务、PostgreSQL checkpoint、进程被杀死后的恢复、只读根文件系统、两个任务并行、操作幂等、统一成本记录。

## 本阶段要改的模块

- `src/ase/config.py`：checkpoint 后端和数据库池参数。
- `src/ase/db/session.py`、`orm.py`、新的 Alembic 修订：连接失败、操作账本、模型用量、会话标识。
- `src/ase/app.py`：按配置选择 SQLite 或 PostgreSQL checkpointer。
- `src/ase/workflow/graph.py` 与 `service.py`：幂等恢复，不把大型事件放进 State。
- `src/ase/adapters/openhands.py`：按已有 conversation id 重新连接。
- `src/ase/infra/agent_container.py`：label、采纳仍在运行的容器、根文件系统试验。
- `src/ase/infra/git_workspace.py`：已存在的任务 worktree 直接复用。
- `src/ase/infra/policy.py`：运行时权限等级。
- 新增测试和本阶段文档。不改 OpenHands 上游，不重写 Coding Agent。
