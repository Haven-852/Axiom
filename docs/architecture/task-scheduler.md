# 任务调度

`src/ase/execution/task_scheduler.py` 从当前 Plan 的 `validation_result.topological_order` 选择下一个任务。它不使用模型返回的 `execution_order` 作为排序依据。

候选任务必须属于当前 Plan，状态是 `ready`，依赖都已 `completed`，没有阻塞澄清，也没有活动 Execution。

优先级只在同一依赖深度内排序。更深的任务不能因为优先级更高而先执行。本阶段每次只返回一个任务。

不属于当前 `plan_id` 的任务视为被替代，调度器不会选中它们，也不会改写旧行。`in_progress` 表示已经领取。没有新增 `running` 或 `superseded` 状态，也没有 Migration。
