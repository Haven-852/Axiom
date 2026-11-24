# 执行资格门禁

编码任务在进入 CodingAgent 和 `ExecutionService.run_claimed` 之前，必须通过 `src/ase/execution/eligibility.py`。API 和 LangGraph 节点不能代替这次检查。创建 Execution 前会再查一次。

## 拒绝条件

- 当前 Plan 版本不存在，或请求的任务不属于这个版本。
- Plan Hash 与版本记录不一致。
- DAG 校验不是有效。
- 没有绑定当前 Artifact、版本和 Hash 的 `approved` 审批。
- 任务上仍有 `unresolved` 或 `deferred` 的阻塞澄清。错误里带有 `clarification_id`。
- 任务不是 `ready`。恢复已经领取的任务时允许 `in_progress`。
- 依赖任务还没有 `completed`。
- Workflow 是 `failed` 或 `cancelled`。
- 同一任务已有活动 Execution，或 PostgreSQL advisory lock 没有拿到。
- 调用方给出了仓库标识，但当前 Workflow 没有持有未过期的仓库锁。

拒绝时不调用 CodingAgent，不创建 Execution，不启动 Docker。

`planned` 不是批准。`ready` 也不是执行授权。calculator Workflow `1dd7c84d-36d1-4dcb-a66e-1004e0a1985f` 的 Version 2 没有批准，因此它的 ready 任务同样被拒绝。

已有 Plan Version 的 Workflow 不能通过旧生产图的 `resume` 进入编码。没有 Plan Version 的历史工作流仍恢复旧图。细节见 `docs/reports/phase-2.5-production-entry-audit.md`。

## 并发

领取和检查在同一个事务里对 Workflow 行加锁，然后把任务从 `ready` 改成 `in_progress`。PostgreSQL 上再对任务使用 `pg_try_advisory_lock`，锁住实际执行过程。另一个请求得到冲突。SQLite 会忽略行锁，并发测试放在 PostgreSQL。
