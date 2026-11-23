# ADR-004 工作区隔离

状态：已接受。本 ADR 改写 0001 里“ASE 不执行 git worktree add、不自己启动容器”的实现条款。

Phase 1 规格要求 ASE 拥有 `GitWorkspaceManager`，并且容器必须有内存、CPU、权限和 socket 限制。`DockerWorkspace` 源码里的 `docker run` 不带 `--memory`、`--cpus`、`--cap-drop` 或 `no-new-privileges`。调用它无法满足规格，子类化它的私有 `_start_container` 又会绑住上游实现。

因此：

- ASE 用 git worktree 创建 `ase/<task-id>` 分支。
- ASE 用 `agent_server_run_args` 生成加固后的 agent server 容器命令。
- 容器起来之后，适配器用 `RemoteWorkspace` 连接它。
- `LocalWorkspace` 只在没有 `ASE_OPENHANDS_HOST` 时使用，并且路径必须通过 `assert_workspace_allowed`。

0001 关于“不复制 OpenHands 源码、不让节点直接依赖 SDK”的决定继续有效。
