# 安全边界

`GitWorkspaceManager` 用 `git worktree add -b ase/<task-id>` 创建任务分支。`commit_changes` 拒绝 `main` 和 `master`。`merge_to_main` 固定抛出 `PermissionError`。本阶段没有自动合并。

`assert_workspace_allowed` 要求路径位于 `ASE_WORKSPACE_ROOT` 之下，并且不能落在 ASE 源码的 `src`、`docs`、`analysis`、`tests` 里。

Docker 参数由 `DockerSandbox.build_pytest_args` 和 `agent_server_run_args` 生成。两条命令都包含内存、CPU、pid 限制、`--cap-drop=ALL` 和 `no-new-privileges`。命令里出现 `--privileged` 或 `docker.sock` 会直接拒绝。

pytest 容器网络是 `none`。依赖 pytest 时，ASE 在宿主机把 pytest 装进工作区 `.vendor`，容器只读这个目录，不在容器里访问网络。agent server 容器使用 `bridge`，因为模型 API 在容器外；它仍然不挂载 Docker socket，端口只绑定 `127.0.0.1`。

审批接口要求请求头 `X-ASE-API-KEY`。通过和拒绝都会写 `audit_logs`。

危险命令片段由 `review_command` 拒绝，包括 `docker.sock`、`--privileged`、`rm -rf /`、`mkfs`。

计划审批通过后，编码适配器把 OpenHands confirmation policy 设为 `NeverConfirm`，并且只注册 `file_editor`。终端工具没有接上。这一层是已批准工作区里的文件编辑，不是第二次计划审批。ASE 自己启动的 pytest 和 agent server 命令仍然走上面的容器策略。
