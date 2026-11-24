# AXIOM / ASE

ASE 是自主研发流程的控制面。Phase 1 用 FastAPI 和 LangGraph 编排需求、计划、人工审批、git worktree、编码适配器、测试和交付。编码执行面是 OpenHands SDK 1.63.1。

当前主线：

- Phase 2.1 Research Agent 读取仓库并写出 ResearchReport。
- Phase 2.2 Planning Agent 生成 Plan 和 Task DAG。Workflow 终态是 `planned`。
- Phase 2.3 保存澄清回答、Plan 版本和人工审批。`planned` 不是 `approved`。
- Phase 2.4 只对当前已批准的 Plan 提供显式编码入口。资格门禁在 CodingAgent 之前执行。未批准的计划不会创建 Execution。
- Phase 2.5 用独立仓库完成了一次真实 DeepSeek 编码、容器内 pytest、评估和交付。带 Plan Version 的 Workflow 不能从旧生产图恢复进入编码。

编码入口是 `POST /api/v1/workflows/{workflow_id}/coding-tasks/{task_id}`，请求头 `X-ASE-API-KEY`。它使用独立的 LangGraph 线程，不恢复生产编码图。已有 Plan Version 时，旧批准接口的 resume 返回 409。

七个上游仓库的源码分析在 `analysis/`：

- `repository-comparison.md`
- `architecture-analysis.md`
- `agent-design-analysis.md`
- `workflow-analysis.md`
- `execution-engine-analysis.md`
- `security-analysis.md`
- `code-reuse-mapping.md`
- `technology-selection.md`

V1 控制面决定在 `docs/adr/0001-ase-v1-control-plane.md`。Phase 1 规格改写了其中的 worktree 和容器启动方式，说明在 `docs/adr/ADR-004-workspace-isolation.md`。设计草案 `docs/design/ase-v1.md` 仍然保留。

## 本地启动

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
copy .env.example .env
.\.venv\Scripts\python -m ase.main
```

API 文档在 `http://127.0.0.1:8000/docs`。审批请求头是 `X-ASE-API-KEY`。

PostgreSQL：

```powershell
docker compose up -d postgres
```

然后把 `ASE_DATABASE_URL` 设为 `postgresql+psycopg://ase:ase@127.0.0.1:5432/ase`，安装 `pip install -e ".[postgres]"`，再执行 `alembic upgrade head`。

PoC 步骤见 `docs/poc.md`。
