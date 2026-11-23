# 恢复策略

恢复按故障发生的位置选择动作。业务状态在 PostgreSQL。LangGraph checkpoint 使用同一数据库里的 `PostgresSaver` 表，不和业务表混写。OpenHands 会话文件在 worktree 的 `workspace/conversations/<conversation-id>`，不写入 checkpoint。

审批已经暂停，ASE 进程被杀死：checkpoint 仍停在 `human_approval`。新进程查询到 `waiting_approval`。批准使用原来的 workflow id。计划节点不会重放，所以 Task 不会重复。worktree 已存在时复用 `ase/<task>` 分支。

编码正在执行，ASE 进程被杀死：容器使用 `docker run -d`。进程被 `TerminateProcess` 杀掉时不会执行 `stop_all()`。容器带 `ase.role=agent` 和 workspace 标签。新进程按标签采纳容器，并从 `docker inspect` 读取会话密钥，不把密钥写入业务表或日志。Execution 上已经保存的 `conversation_id` 被传回 `RemoteConversation`。已有用户消息时不再 `send_message`。

Agent Server 进程重启：客户端在断开类错误上等待 `/health`，用同一 host、同一会话密钥和同一 conversation id 重建 `RemoteWorkspace`。服务端把被打断的会话标成 `paused` 时，ASE 再调用一次 `run()` 继续原会话。仍然是 `paused` 时，执行状态记为 `interrupted`，不另发一条用户消息。Attempt 的身份是 `execution_operations.id`，格式为 `{workflow_id}:{task_id}:{kind}:{attempt}`。同一次 develop 继续用原来的 attempt。修复才增加 attempt，并使用新的 conversation id。

测试已经通过，评估和交付还没写入：`ASE_CRASH_POINT=before_final_commit` 在 `_evaluate` 开头退出进程。测试节点已经把通过的 `TestResult` 写入 PostgreSQL。新进程从 checkpoint 继续。若该 execution 已有通过的测试结果，测试节点直接复用，不再执行 pytest。随后补写 EvaluationReport、Artifact 和任务分支提交。`before_execution_commit` 仍是编码返回之后、Execution 完成状态写入之前的退出点。

容器消失但 worktree 还在：pytest 发现容器不存在时，会按同一 worktree 再启动容器。宿主机 pytest 保持关闭，缺少容器时退出码是 127。

交付提交排除 `workspace/conversations` 和 `workspace/bash_events`。Git 调用带 `core.longpaths=true`，避免 Windows 路径长度挡住任务分支提交。会话文件留在 worktree，不进入交付提交。

PostgreSQL 不可用时，创建请求不会返回成功。`OperationalError` 返回 503，正文是 `database unavailable`，不包含连接串。连接恢复后 `pool_pre_ping` 允许继续使用原来的数据。事件写入在同一个 `session_scope` 里，异常会回滚。

取消：`POST /api/v1/workflows/{id}/cancel` 把未结束的工作流标成 `cancelled`。之后的批准返回 409。这个接口不调用 `stop_all()`，避免停掉其他任务的容器。已经发出的模型请求不会被收回。

这些机制保证的是可恢复的 at-least-once。同一操作在 `completed` 之后会被跳过。模型请求一旦已经发出，不能收回。
