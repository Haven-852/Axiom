# Task DAG

规划复用 Phase 1 的任务表，不新增任务系统，也不做数据库 Migration。

## 表

- `development_plans`：一条 Plan 对应一行。`requirement_id` 连接 Workflow 所使用的需求。
- `tasks`：一行一个任务。`status` 使用已有的 `ready` 或 `blocked`。`agent_id` 保存任务类型，不写 `coding`。
- `task_dependencies`：`predecessor_id` 必须在 `successor_id` 之前。

优先级、验收标准、相关文件、finding id、criterion id 和澄清阻塞关系放在 `kind=plan` 的 Artifact 里，并用同一个 `task_id` 关联。

## 校验

DAG Validator 在本地执行，不信任模型给出的边：

1. `task_id` 唯一。
2. 依赖必须指向本计划中的任务，不能自依赖，不能成环。
3. 可以完成拓扑排序。
4. `execution_order` 必须正好是可执行任务的拓扑序。
5. 每个任务都有验收标准，并且至少引用一条真实 finding 或 criterion。
6. `related_files` 必须是研究报告里出现过的相对路径，拒绝绝对路径和 `..`。
7. `inferred` 验收标准必须在 assumptions 里标明是推断。
8. 未确认范围不能被写成已确认需求。
9. 澄清项状态必须是 `unresolved`。

结构错误时 `valid` 为 false，不写任务。存在被阻塞的任务时，计划仍可以是结构合法的：`blocked_task_count` 与 `executable_task_count` 分开计数。

## 阻塞

研究报告里的澄清问题一律保持未回答。只要还存在这类问题，`implementation`、`testing` 和 `refactoring` 就是阻塞任务。依赖这些任务的后续任务也会被传递阻塞。`documentation` 和 `investigation` 只有在不依赖阻塞任务、并且不把未确认范围写成需求时才进入 `execution_order`。

`ready` 只表示规划上可以继续讨论或记录，不表示可以交给编码运行时执行。
