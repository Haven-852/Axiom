# ADR-013 Repository Lock

状态：已接受。

仓库身份使用 Git common directory，而不是项目 id 或原始路径。

跨进程互斥使用 PostgreSQL 租约。过期可接管，释放校验持有者。冲突是 409，不是无限等待。不使用 `asyncio.Lock` 或进程内字典作为权威锁。

这改变了 Phase 1.2 的双项目并发测试：两个项目指向同一仓库时，第二个 start 返回 409。隔离测试改为两个独立仓库副本。Worktree 仍按任务隔离。
