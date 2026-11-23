# ADR-016 Plan 审批与澄清版本

状态：已接受。

Phase 2.3 只增加计划审查、澄清、版本和人工审批。不实现 CodingAgent，不把批准解释成编码授权。

## 决定

复用 `approvals` 表。Alembic `0004_phase23` 把唯一约束改成 `(workflow_id, subject)`，并增加状态 `changes_requested`。计划审批的 subject 带上 Plan Artifact id 和 Plan Hash。相同决定幂等，矛盾决定返回 409。

Plan 版本、澄清回答和延后记录使用新的 Artifact kind，不覆盖历史 Plan body，也不新增审批表或任务表。重新规划复用 PlanningAgent。Workflow 终态继续是 `planned`。`plan-review:` 审批禁止走会 resume 编码图的旧接口。

未解决的阻塞澄清不能被批准绕过。`ready` 只在人工批准当前版本后写给可执行任务，编码图仍不能据此领取任务。

## 后果

一个 Workflow 可以有多个 Plan 版本和多条审批。当前版本是最大版本号。单用户身份是 `api-key`。语义保持 at-least-once。
