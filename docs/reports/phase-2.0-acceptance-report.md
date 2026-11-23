# Phase 2.0 验收报告

日期：2026-11-23。阶段名称：Agent Architecture & Orchestration Foundation。

Phase 2.0 按验收清单完成基础架构后停止。没有进入 Phase 2.1，没有实现完整 Research、Planning、Testing、Diagnosis、Evaluation Agent。

## 完成的功能

- AgentSpec 补齐 version、capabilities、enabled、max_retries。新增 AgentRun、AgentRunStatus、AgentContext、AgentResult。
- AgentRun 状态机只允许约定跳转，不替代 Execution 状态机。
- 进程内 AgentRegistry 和 AgentRunner：输入输出校验、超时、取消、脱敏、幂等键、用量和 Artifact。
- AgentRun、Execution 关联和仓库租约写入 PostgreSQL。Alembic `0003_phase20`。
- Repository Lock：Git common directory 身份，租约过期和持有者校验，冲突为 409。
- CodingAgent 只调用 ExecutionService。生产图 `_run_coding` 和 `src/ase/adapters/openhands.py` 的恢复逻辑没有改写。
- 编排示例：ResearchStubAgent 然后 EchoAgent。Checkpoint 与 AgentRun 分表。
- 取消协议：CancellationContext 看本地标志和 Workflow 状态。CodingAgent 在执行前后检查。`ExecutionService.propagate_cancel` 可以中断已登记的 adapter session，并在有 container id 时停止该容器。

## 核心文件

新增：

- `src/ase/agents/base.py`、`lifecycle.py`、`schema.py`、`cancellation.py`、`examples.py`、`runner.py`、`coding_agent.py`
- `src/ase/execution/service.py`
- `src/ase/infra/repository_lock.py`
- `src/ase/orchestration/`
- `alembic/versions/0003_phase20.py`
- `tests/test_phase20.py`、`tests/test_phase20_postgres.py`

修改：

- `src/ase/contracts/domain.py`、`errors.py`、`config.py`、`db/orm.py`、`store.py`
- `src/ase/agents/registry.py`
- `src/ase/workflow/service.py`：启动时获取仓库租约，取消时传播到 ExecutionService
- `src/ase/workflow/graph.py`：已有通过的 TestResult 不再重复 pytest。这条跳过没有用第二次真实模型崩溃复验
- `alembic/versions/0002_phase12.py`：空库已经由当前模型建表时，增量步骤跳过已存在对象
- `tests/test_isolation.py`：两个项目使用两份仓库副本

## 数据库

新增表：`agent_runs`、`agent_run_executions`、`repository_leases`。

`model_usages` 增加可空列 `agent_name`、`agent_run_id`。

`agent_runs.workflow_id` 外键指向 `workflows.id`。同一 Workflow 的同名 Agent 可以有多行。幂等键唯一。

Migration：`0003_phase20`，上一版本 `0002_phase12`。

## 接口

没有新增公开 HTTP 路由。内部入口是 `AgentRegistry`、`AgentRunner.run`、`CodingAgent.run`、`ExecutionService.run_claimed`、`ExecutionService.propagate_cancel`、`acquire_repository_lease`。

## 测试

非真实模型套件 45 项全部通过，0 失败。命令：

```text
pytest tests --ignore=tests/test_phase121_live.py --ignore=tests/test_real_poc.py --ignore=tests/test_real_repair.py --ignore=tests/test_openhands_resume.py
```

随后单独重跑 `tests/test_phase20_postgres.py`，补上 PostgreSQL 租约冲突断言，通过。

分布：

| 文件 | 数量 | 证据 |
| --- | --- | --- |
| test_phase20.py | 12 | Level 3/4。SQLite、ScriptedCodingAdapter、SqliteSaver |
| test_phase20_postgres.py | 1 | Level 1 基础设施。真实 PostgreSQL、空库和已有库升级、容器重启、外键、事务回滚、租约冲突 |
| test_persistence.py、test_postgres.py | 2 | Level 1 基础设施。PostgreSQL checkpoint 和重启 |
| test_faults.py | 5 | Level 2。本地超时、容器超时、PostgreSQL 暂停、缺镜像。不是 DeepSeek 故障 |
| test_isolation.py、test_process_recovery.py、test_workflow.py 等 | 其余 | Level 3 Scripted，或 Docker 安全配置 |
| test_openhands_sdk.py | 1 | Level 4。TestLLM，不是外部模型 |

本阶段没有重跑：

- `tests/test_phase121_live.py` 的真实崩溃、Agent Server 重启和双 DeepSeek 任务
- `tests/test_real_poc.py`、`tests/test_real_repair.py`、`tests/test_openhands_resume.py`

这些文件仍是 Phase 1.1 / 1.2.1 的 Level 1 记录。Phase 2.0 没有把它们改写成通过。

## 证据等级说明

ResearchStub 和 Echo 的通过是确定性测试。Scripted CodingAgent 证明第二次相同 attempt 不会再次 `execute_task`。这不是真实 DeepSeek 编码。

PostgreSQL 迁移、重启和租约冲突是真实数据库测试，不是真实模型测试。

## 已知问题

- 测试结果跳过已有单元测试。Phase 1.2.1 那次真实崩溃里，同一 Execution 仍跑了两次 pytest。跳过逻辑没有再用真实模型崩溃复验。
- 双真实模型任务当时都交付了。修正后的 conversation 集合互斥断言没有再跑双模型实验。
- 取消可以改 Workflow、记 AgentRun `cancelled`，并调用 session interrupt 和指定容器 stop。已经发出的模型 HTTP 不能撤回。取消不是只改一个字段，也还不是完整的 Conversation 级撤销。
- Agent 超时若发生在返回之前，Runner 收不到这次返回值里的 Artifact。ExecutionService 已经提交的 Execution 会保留。
- 语义仍是可恢复的 at-least-once，不是 exactly-once。
- 只读根文件系统仍是显式开关。
- 生产编码路径没有改接 Agent Runner。新编排图只跑 Stub 和 Echo。

## 下一阶段建议

先做真实 Research Agent，再做 Planning Agent。两者都走 Agent Runner，输出必须是 schema 校验后的结构化对象，并写入 AgentRun。它们默认不创建 Execution，也不导入 OpenHands。

Research 读取需求，产出可被 Planning 消费的发现和验收标准。Planning 把这份结构展开成现有 Task DAG，再交给已经存在的生产工作流或 CodingAgent。不要在这一步做多 Agent 并行改同一仓库。Repository Lock 已经拒绝同一仓库的第二个写 Workflow。

Coding 仍应留在 ExecutionService 后面。接入生产图之前，先用一次真实 DeepSeek 证明 CodingAgent 复用了原 Conversation，而不是另开一条恢复路径。
