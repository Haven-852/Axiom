# Phase 2.5：生产入口审批边界

## 结论

Plan 管理的任务不能再从旧生产图恢复进入编码。没有 Plan Version 的历史工作流仍走旧图。审批校验没有被默认关闭。

## 可以启动旧生产图的入口

| 入口 | 行为 |
| --- | --- |
| `POST /api/v1/workflows` | `WorkflowService.start` 创建新 Workflow，并调用 `src/ase/workflow/graph.py`。新 Workflow 没有 Plan Version。它使用图内的批准，不使用 Plan Approval Service。 |
| `POST /api/v1/approvals/{id}/approve` 和 `reject` | 主题以 `plan-review:` 开头时返回 409，不恢复编码。其他主题调用 `WorkflowService.resume`。 |
| `WorkflowService.resume` | Workflow 已有 `plan_version` Artifact 时返回 409：`plan-managed workflow uses the coding task entry`。没有 Plan Version 时恢复旧图检查点。 |

## 可以创建 Execution 的入口

- 旧图的 develop 节点，只服务上面的旧工作流。
- `CodingAgent` → `ExecutionService.run_claimed`。HTTP 路径是 `POST /api/v1/workflows/{id}/coding-tasks/{task_id}` 和 `POST /api/v1/workflows/{id}/coding-runs`。这两条路径先经过 Eligibility Gate。

没有公开 API 直接调用 `ExecutionService`。

## 可以领取 Task 的入口

`CodingTaskService._execute` 在 Gate 通过后把 `ready` 改成 `in_progress`。旧图创建自己的任务，不领取 Plan 里的任务。

## 绕过检查

| 问题 | 结果 |
| --- | --- |
| 绕过 Approval Service 来编码 Plan 任务 | HTTP 编码入口不能。旧图 resume 在存在 Plan Version 时被拒绝。 |
| 绕过 Eligibility Gate | HTTP 编码入口不能。Gate 对未批准、旧版本、哈希不一致、未解决或推迟的阻塞澄清、阻塞任务、未完成依赖和取消的 Workflow 返回 409。 |
| API 直接调用 ExecutionService | 没有这种路由。 |
| 内部服务绕过受控入口 | `CodingAgent` 在输入没有 `plan_id` 时仍直接调用 `run_claimed`。这是 Phase 2.0 的兼容路径，现有复用测试依赖它。HTTP 编码 API 总会带上 `plan_id`。 |
| 旧 Workflow 恢复时跳过新校验 | 没有 Plan Version 的历史 Workflow 继续恢复，不套用新 Gate。有 Plan Version 的 Workflow 不能从旧图恢复。 |

## 不采取的做法

没有删除旧生产图，也没有让历史 Workflow 因为新 Gate 无法恢复。没有用“默认不检查批准”来兼容旧路径。
