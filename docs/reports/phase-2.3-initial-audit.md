# Phase 2.3 初始审计

日期：2026-11-23。审计对象是 Phase 2.2 完成时的 Approval、Plan、Task 和 Workflow。本文件记录当时已经存在的能力、缺口，以及随后采用的最小扩展。没有新建第二套审批表，也没有把 `planned` 改成 `approved`。

## 1. 已有审批能力

`approvals` 表和 `Approval` 领域对象已经存在。字段是 `project_id`、`workflow_id`、`subject`、`status`、`comment`，以及创建和更新时间。状态原来只有 `pending`、`approved`、`rejected`。

编码图在 `_request_approval` 中插入一条 `pending` 审批，并把 Workflow 设为 `waiting_approval`。人工节点通过 `set_approval` 把这一行改成 `approved` 或 `rejected`。批准后 Workflow 进入 `developing`，编码图继续执行。

API `POST /approvals/{id}/approve` 和 `POST /approvals/{id}/reject` 会调用 `WorkflowService.resume`。这个 resume 使用 Workflow 自己的 thread id，会继续编码图。

`audit_logs` 已经能记录操作者、动作、对象和时间。

## 2. 缺失的审批能力

Phase 2.2 结束时，审批不能直接承载 Plan 版本：

- 唯一约束 `uq_approvals_workflow` 使一个 Workflow 只能有一行审批。规划审批和以后的编码审批会互相覆盖或互相挡住。
- `save_approval` 只要该 Workflow 已有任意审批就直接返回，不会按 subject 区分。
- 没有 `plan_id`、`plan_version`、`plan_artifact_id`、`plan_hash` 或审批人列。
- 没有 `changes_requested`。
- `set_approval` 会覆盖状态和 comment，不能保留“先批准再拒绝”的冲突。
- 审批通过会 resume 编码图。计划审批如果走这条路径，会在用户还没有授权编码时启动 Coding Workflow。

因此现有 Approval 行可以复用，但不能原样用于 Plan 审批。

## 3. Task 能否表达澄清后的阻塞

可以。`tasks.status` 已有 `ready` 和 `blocked`。`ready` 只表示规划层面没有未解决依赖，不是执行许可。编码图不会因为 `ready` 去领取任务。

缺口是：任务行没有版本号。新 Plan 如果改写旧 `plan_id` 下的任务，就会把 Version 1 和 Version 2 混在一起。最小做法是新版本使用新的 `plan_id` 和新的任务 id，旧行保持不动。

## 4. Plan 是否已经版本化

没有。Phase 2.2 的计划是一条 `kind=plan` 的 Artifact。成功计划只有一条：

- Artifact `b5b35a61-01a0-4971-a6b3-3e0c8587bcbc`
- Plan `06e3507e-7263-5e77-98b0-4769b732cca5`
- AgentRun `0fbc7053-40db-478e-83b8-66d9cc15394d`

Artifact 按 `workflow_id + uri` 去重，没有版本指针，也没有父 Artifact 字段。直接修改 `body` 会覆盖历史。澄清回答和审批也不能写回这条 Artifact。

## 5. Workflow 状态

规划成功后的状态是 `planned`，并且这是规划工作流的终态。它不表示计划已批准。取消操作不会把 `planned` 改成 `cancelled`。不需要为了审批增加 Workflow 状态。

## 6. 是否需要 Migration

需要一次最小 Migration。`approvals.subject` 已是文本列，`status` 是 `String(32)`，可以放下 `changes_requested`，不必改列类型。

必须改掉“一个 Workflow 一行审批”的唯一约束，否则同一 Workflow 的计划审批和编码审批无法并存，多个 Plan 版本也无法各有一条决定。

不新增 Approval、Task、Artifact 或 AgentRun 表。

## 7. 采用的最小方案

1. Alembic `0004_phase23` 删除 `uq_approvals_workflow`，新增 `uq_approvals_workflow_subject (workflow_id, subject)`。
2. `save_approval` 只在相同 `workflow_id + subject` 已存在时跳过。
3. 计划审批的 subject 是 `plan-review:{plan_artifact_id}:{plan_hash}`。comment 用 JSON 保存 actor、comment、plan_id、plan_version、plan_artifact_id、plan_hash 和 affected_task_ids。
4. 增加状态 `changes_requested`。同一 subject 的第一次终态决定胜出。相同决定返回原行。不同决定返回 409，不覆盖。
5. 旧的 `/approvals/{id}/approve|reject` 如果 subject 以 `plan-review:` 开头，返回 409，不调用 resume。
6. Plan 版本、澄清回答和延后记录使用新的 Artifact kind：`plan_version`、`clarification_answer`、`clarification_deferral`。不修改已有 `kind=plan` 的 body。
7. 当前版本是该 Workflow 下最大的 `plan_version`，不另建指针列。
8. 重新规划复用 `PlanningWorkflowService`。空决策的规划幂等键保持 Phase 2.2 的值。
9. 人工批准前，实现类任务即使已经进入 `execution_order`，数据库状态仍是 `blocked`。只有批准当前版本后，才把同时出现在拓扑序和 `execution_order` 中的任务改成 `ready`。
10. Workflow 保持 `planned`。批准不调用 `WorkflowService.start` 或 `resume`。
