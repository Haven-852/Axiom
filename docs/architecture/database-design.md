# 数据库

ORM 定义在 `src/ase/db/orm.py`。迁移是 `alembic/versions/0001_phase1.py`。

表：`projects`、`requirements`、`requirement_analyses`、`development_plans`、`tasks`、`task_dependencies`、`approvals`、`workflows`、`workflow_checkpoints`、`executions`、`execution_events`、`artifacts`、`test_results`、`evaluation_reports`、`agent_registry`、`audit_logs`。

主键是字符串 UUID。外键连接项目、需求、计划和任务。`tasks.status` 使用 `TaskStatus`：`pending`、`ready`、`in_progress`、`blocked`、`completed`、`failed`、`cancelled`。

JSON 列只用于验收标准、依赖之外的扩展 payload、事件 payload 和 agent spec。需求正文、任务说明、测试输出和 diff 正文是独立列。

开发默认库是 sqlite，`docker-compose.yml` 提供 PostgreSQL 16，宿主机端口是 5433。连接串是 `postgresql+psycopg://ase:ase@127.0.0.1:5433/ase`。业务单元测试继续使用 sqlite。

LangGraph 检查点默认写到 `checkpoints.sqlite`。它和业务库是两个存储。
