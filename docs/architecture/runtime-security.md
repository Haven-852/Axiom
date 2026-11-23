# 运行时安全

权限由 ASE 的命令策略和 OpenHands 的确认策略一起执行，不靠提示词。

| 等级 | 例子 | ASE 动作 |
| --- | --- | --- |
| L1 | 读取和修改授权 worktree | 允许 |
| L2 | pytest、ruff、mypy | 允许，并记录命令、退出码和耗时 |
| L3 | sudo、chmod、删除大量文件、合并或推送主分支、Docker socket、特权模式 | 拒绝 |

远端编码会话继续使用 `ConfirmRisky(threshold=MEDIUM)`。HIGH 和未知动作被拒绝。Terminal 工具仍在，因为 Phase 1.1 已经验证的路径使用它；L3 命令不会因此放行。

只读根文件系统不是默认值。官方镜像里的 PyInstaller 入口在 `--read-only` 下需要可执行的 `/tmp`，否则动态库映射失败。`ASE_AGENT_READONLY_ROOT=true` 时增加：

- `--read-only`
- `/tmp` 和 `/var/tmp`、`/home/openhands` 的可执行 tmpfs
- `/run` 的 tmpfs

任务文件只通过 `/workspace` 挂载。这个模式用自建镜像 `ase-agent-server:1.63.1` 验证过。镜像从官方标签安装 pytest，再切回 `openhands` 用户。默认仍使用官方标签和可写根文件系统，避免在官方镜像里临时 `pip install`。

容器继续拒绝 `--privileged` 和 `docker.sock`。内存、CPU、pid、`CapDrop=ALL` 和 `no-new-privileges` 由 inspect 核对。
