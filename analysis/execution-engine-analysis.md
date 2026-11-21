# 代码执行引擎

ASE 的执行边界必须能换成非 Python 实现。这一节只记录源码里真实的执行路径。

## 文件、Shell、Git、Workspace

### SDK 的 Workspace 接口

`openhands-sdk/openhands/sdk/workspace/base.py` 的 `BaseWorkspace` 把执行收成三类抽象方法：

- `execute_command(command, cwd, timeout) -> CommandResult`
- `file_upload(source_path, destination_path) -> FileOperationResult`
- 同文件中还有对应的下载抽象（与 upload 成对）

`CommandResult` 和 `FileOperationResult` 在 `workspace/models.py`。实现：

- `LocalWorkspace.execute_command`：`workspace/local.py`
- `RemoteWorkspace.execute_command`：`workspace/remote/base.py`，对端是 HTTP
- `AsyncRemoteWorkspace.execute_command`：`workspace/remote/async_remote_workspace.py`
- `DockerWorkspace`：`openhands-workspace/openhands/workspace/docker/workspace.py`。它启动预构建的 agent-server 镜像，健康检查通过后，把文件和命令转发到容器里的 HTTP API。类文档写明它不负责现场构建镜像。

同目录还有 `apptainer/workspace.py`、`cloud/workspace.py`、`remote_api/workspace.py`。语言无关的扩展点就是这组方法：Go、C、C++ 执行器实现同一套 HTTP，Python 侧继续用 `RemoteWorkspace`。

Agent 不直接碰文件系统。`TerminalTool`、`FileEditorTool`（`openhands-tools/openhands/tools/terminal/definition.py`、`file_editor/definition.py`）通过工具 executor 使用会话绑定的 workspace。agent-server 另外暴露 HTTP：`bash_router` 前缀 `/bash`，`file_router` 前缀 `/file`，`git_router` 前缀 `/git`（`openhands-agent-server/openhands/agent_server/`）。

### Git Worktree

创建逻辑在 agent-server，不在 `Agent.step` 里。`conversation_service.py`：

- `_plan_conversation_worktree` 用 `git rev-parse --show-toplevel` 确认仓库，算出 `conversation_worktree_root / conversation_id / repo_name`，分支名是 `options.branch_prefix + conversation_id`。
- `_create_conversation_worktree` 执行 `git worktree add -b <branch> <path> <start_point>`，返回 `LocalWorkspace(working_dir=plan.workspace_dir)`。
- 选项模型在 SDK `conversation/request.py` 的 `ConversationWorktreeOptions`：`base` 为 `default_branch`、`head` 或 `ref`，`branch_prefix` 默认值由 `DEFAULT_WORKTREE_BRANCH_PREFIX` 提供。`ConversationConfig.worktree` 的字段说明写明默认根是 `/tmp/conversation-worktrees/<conversation_id>/<project_name>`。
- 删除在 `local_storage.py` 的 `remove_conversation_worktree`：删目录后在主仓库执行 `git worktree prune`。注释写明分支保留。

非 git 目录会在 `_plan_conversation_worktree` 捕获 `GitCommandError` / `GitRepositoryError` 后返回 `None`。测试 `tests/agent_server/test_conversation_service.py` 覆盖了这个分支。

子会话隔离同样可以要 worktree，见 `tests/tools/child_conversation/test_start_child_conversation.py` 的 `"isolation": "worktree"`。

### Docker 运行时（服务端）

除了 `DockerWorkspace`，agent-server 还有 `openhands/agent_server/docker_runtime/`：`routers.py` 定义 `docker_conversation_router`、`docker_workspace_router`、`docker_sockets_router`。`api.py` 在 `config.conversation_runtime_selectable` 时挂上 `RuntimeDispatchMiddleware`。这是“一个 FastAPI 进程按会话把请求派到容器”的结构。ASE 若采用模块化单体，更稳妥的是进程内 `LocalConversation` 加 `DockerWorkspace`，而不是先吞下整套 docker runtime 路由。

### 错误诊断

SDK 没有独立的“自动修复服务”。诊断发生在循环内部：

- 工具错误经 `Agent._emit_tool_error` 变成事件，模型下一轮能看见。
- `StuckDetector` 识别重复失败并 `_check_stuck_or_nudge`。
- `CriticMixin`（`agent/critic_mixin.py`）在动作后做批评和迭代细化。
- `GoalController` 加 `judge_goal` 决定目标是否完成。

这些都是“把失败交回模型”，不是静态分析器。

## mini-swe-agent

`Environment` 协议只有 `execute(action, cwd) -> dict`、`get_template_vars`、`serialize`（`src/minisweagent/__init__.py`）。动作就是 `{"command": ...}`。没有单独的读文件工具；读写都靠 shell。

- `LocalEnvironment.execute` 用 `subprocess`，超时杀进程组（`environments/local.py` 的 `_run`）。
- `DockerEnvironment.execute` 组装 `docker exec -w <cwd> <container_id> bash -lc <command>`（`environments/docker.py`）。容器由 `_start_container` 以 `docker run -d` 启动，默认 `run_args=["--rm"]`。
- 另有 `SingularityEnvironment`、`environments/extra/bubblewrap.py`、`swerex_docker.py`。`get_environment` 按配置选择。

`interpreter` 默认 `["bash", "-lc"]`，配置注释写明可以改成 python。这是换解释器，不是换实现语言的 RPC。ASE 若要 Go 执行器，应在这个协议外面加一层 HTTP，让 `execute` 的远端不再是 `docker exec` 字符串拼接。

Git：默认循环不调用 git 库。仓库操作只能是模型发出的 shell。

## aider

文件编辑不走通用工具，而走 `Coder.apply_edits` / `apply_updates`（`aider/coders/base_coder.py`）。格式由子类解析（SEARCH/REPLACE、udiff、整文件）。`allowed_to_edit` 限制可写路径。

Shell：`aider/run_cmd.py` 的 `run_cmd` 在 Windows 用 `run_cmd_subprocess`，其他平台可用 `run_cmd_pexpect`。`Commands.cmd_run` 和 `Coder.run_shell_commands` 调用它。没有容器隔离。

Git：`GitRepo`（`aider/repo.py`）使用 GitPython。`commit`、`get_diffs`、`get_tracked_files`、`get_dirty_files`。`Coder.auto_commit` 在编辑成功后提交。`Coder.lint_edited` 把 linter 输出变成下一轮反射。`Commands.cmd_test` 跑测试命令。

仓库地图：`RepoMap` 用 tree-sitter 抽符号，减上下文。这是编辑质量能力，不是执行沙箱。

`docker/Dockerfile` 把 aider 本身装进镜像，并 `git config --system --add safe.directory /app`。它不拦截 `run_cmd`。

## MetaGPT

执行环境是 `metagpt/environment/base_env.py` 的 `ExtEnv`，带 gymnasium 的 action/observation space。软件研发路径使用角色动作写文件，另有 `metagpt/utils/git_repository.py` 被 `base_env.py` import。已读代码没有 `DockerWorkspace` 一类的命令沙箱，也没有 worktree。诊断主要是角色自己的 review 动作（`software_company.startup` 有 `code_review` 和 `run_tests` 开关），不是统一的执行器错误类型。

## cline

工具在 `AgentRuntime.executeToolCalls` 里执行，审批通过后才调用工具实现。VS Code 侧的 worktree 是 `apps/vscode/src/utils/git-worktree.ts` 的 `createWorktree`：`simple-git` 参数 `worktree add`，可选 `-b`。扩展里的 `WorktreeServiceClient.createWorktree` 由 `CreateWorktreeModal.tsx` 调用。这是 IDE 帮用户开并行目录，命令仍在该窗口的本地工具里执行，不是远程多语言执行器。

checkpoint 在 `sdk/packages/core/src/session/checkpoint-restore.ts` 和 `hooks/checkpoint-hooks.ts` 的 `createWorktreeStashCommit`。那是会话快照用的 git 对象，和“每个任务一个 worktree”不是同一个函数。

## automation 与 Canvas

两者都不执行构建命令。automation 把执行委托给 agent-server。Canvas 通过 file、terminal、git 的前端适配显示 agent-server 的结果（`src/utils/terminal-commands.ts` 使用 agent-server 事件类型）。

## 多语言接口（ASE 要保留的形状）

建议 ASE 对外只暴露下面这个边界，内部第一版用 Python 实现：

1. `execute(command, cwd, timeout) -> {stdout, stderr, exit_code}`，对齐 `CommandResult` 与 mini-swe 的 `execute` 返回字典。
2. `read_file` / `write_file` / `list`，对齐 `FileOperationResult`，不要把 tree-sitter 或 aider 的补丁解析放进接口。
3. 传输用 HTTP JSON，直接对接 `RemoteWorkspace`，这样 Go、C、C++ 进程不必嵌入 CPython。
4. 任务级隔离用 git worktree 路径作为 `cwd`，由 ASE 在调用执行器之前创建。执行器只认目录，不认“对话”概念。

自动修复留在 Agent 循环：执行器返回非零退出码和输出，SDK 把它变成 Observation，LangGraph 的 verify 节点决定是否再跑一轮。不要在执行器里调用模型。
