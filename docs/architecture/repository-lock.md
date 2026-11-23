# Repository Lock

Project Lock 仍然保证一个项目只有一个未结束 Workflow。Repository Lock 解决两个项目指向同一个 Git 仓库的写冲突。

## 身份

`repository_identity` 执行 `git rev-parse --git-common-dir`，再解析为绝对路径。相对的 common dir 会相对仓库目录解析。Windows 上用 `os.path.normcase`。同一仓库的 worktree 得到同一个 common dir，因此共用一把锁。原始路径字符串不是唯一依据。

## 策略

第一版是保守的写锁：

- 一个仓库同时只有一个活动持有者。
- 不同仓库可以并行。
- 同一持有者重复获取会延长过期时间，不新增行。
- 过期后其他持有者可以接管。
- 释放时校验 `holder_id`。旧持有者不能释放新持有者的锁。
- 冲突抛出 `WorkflowConflict`，API 层返回 409。不等待。

锁是 `repository_leases` 行，默认 1800 秒，配置项 `ASE_REPOSITORY_LEASE_SECONDS`。PostgreSQL 在写入前使用 `pg_advisory_xact_lock(crc32(repository_key))`，事务结束锁就释放。真正的互斥和过期看表行。crc32 碰撞只会让无关仓库串行获取，不会让两个持有者同时写同一行。SQLite 测试没有 advisory lock，依赖租约行。

## 何时获取和释放

`WorkflowService.start` 在保存 Workflow 的同一事务里获取租约。持有者是 workflow id。

Workflow 变成 `delivered`、`failed` 或 `cancelled` 时，删除 `holder_id` 等于该 workflow 的租约。Coding 失败走到终态后锁会释放。进程退出后，过期时间到了就可以接管，不会永久死锁。

Worktree 隔离不变。清理 worktree 属于仓库写操作，应在租约释放之后进行，或者由当前持有者执行。运行中的 Workflow 不会因为清理去抢另一把锁。

同一仓库的不同 worktree 不能同时作为活动写任务。读操作仍按原有权限策略，不另加读锁。
