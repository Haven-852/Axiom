# ADR-018：Plan 管理的执行和旧生产图分开

## 状态

已接受。

## 背景

Phase 2.4 的编码入口会检查计划批准。Phase 1 的生产图仍然从 `POST /workflows` 进入，并在图内的人工批准之后编码。`POST /approvals/{id}/approve` 对非 `plan-review:` 主题会恢复这张旧图。

如果一个 Workflow 已经有 Plan Version，再恢复旧图就会绕过 Eligibility Gate。

## 决策

- 没有 Plan Version 的 Workflow 仍走旧生产图，包括它的检查点恢复、测试、诊断、修复、评估和交付。
- 已有 Plan Version 的 Workflow 调用旧图 `resume` 时返回 409。编码只能走 `CodingTaskService`。
- 不删除旧生产图，也不把审批校验默认关掉。
- CodingAgent 在没有 `plan_id` 时仍可直接委托 ExecutionService。这是内部兼容路径，HTTP 编码入口不会构造这种输入。

## 后果

旧工作流可以恢复。带计划的工作流不能从旧批准接口进入编码。直接调用 Agent Runner 且不带 `plan_id` 的内部调用仍不经过 Gate，这个边界写在生产入口审计里。
