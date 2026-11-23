# PoC 运行

1. 使用 Python 3.12 或 3.13 创建虚拟环境，并安装 `pip install -e ".[dev]"`。
2. 复制 `.env.example` 为 `.env`。本机没有 PostgreSQL 时保持 sqlite URL。
3. 启动 API：`.venv\Scripts\python -m ase.main`。
4. `POST /api/v1/projects`，`repo_path` 指向一个只包含计算器代码的 git 仓库，`base_ref` 为 `HEAD`。
5. `POST /api/v1/requirements`，正文使用：为计算器增加一个支持科学计数法的表达式计算功能，并补充必要的单元测试。
6. `POST /api/v1/workflows`。响应停在 `waiting_approval`。
7. 带 `X-ASE-API-KEY` 调用 `POST /api/v1/approvals/{id}/approve`。
8. 查询 `GET /api/v1/workflows/{id}`、`/events`、`/executions/{id}`、`/artifacts/{id}`。

默认 `ASE_CODING_RUNTIME=scripted`，只用于没有模型密钥时的图和 API 测试。`ASE_CODING_RUNTIME=openhands` 会启动 Agent Server 容器，由真实模型修改 worktree，并在该容器内执行 pytest。容器不存在时测试失败，不会退回宿主机。

真实执行步骤和证据见 `docs/poc/real-agent-execution.md`。
