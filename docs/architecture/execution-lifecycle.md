# Execution 生命周期

状态和允许的后续状态：

| 当前 | 可以进入 |
| --- | --- |
| created | queued、starting、cancelled |
| queued | starting、cancelled |
| starting | running、failed、cancelled |
| running | completed、failed、interrupted、cancelled、timeout |
| interrupted、timeout | running、failed、cancelled |
| completed、failed、cancelled | 无 |

`assert_transition` 拒绝表以外的变化。相同状态的字段更新不算一次转换。旧值 `succeeded`、`pending`、`timed_out` 分别读成 `completed`、`queued`、`timeout`。

一次编码尝试先写 `created`，再进入 `starting` 和 `running`，然后才调用 OpenHands。成功写成 `completed`。超时写成 `timeout`。每次变化都有执行事件。

`execution_operations.attempt` 区分同一次恢复和一次新的修复。修复使用新的 attempt。恢复已有 conversation id 时不增加 attempt，也不创建第二个会话。

一个项目同时只允许一个未结束的工作流。第二个创建请求返回 409。这是项目锁，不是整个 git 仓库的锁。两个项目可以指向同一个仓库并使用不同 worktree。
