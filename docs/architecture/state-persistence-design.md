# 三层状态

ASE 把恢复所需的状态分成三层。它们可以放在同一台 PostgreSQL 里，但不能放进同一张 JSON 表，也不能互相代替。

## 业务库

PostgreSQL 保存 Project、Requirement、Task、Approval、Workflow、Execution、ExecutionEvent、Artifact、TestResult、EvaluationReport 和 AgentRegistry。Phase 1.2 增加两张表：

- `execution_operations`：一次外部操作的意图和结果。主键是 `workflow_id:task_id:kind:attempt`。
- `model_usages`：真实返回的 token 和 SDK 给出的费用。没有读到的数字保持空。

`workflows.thread_id` 只是业务行和 checkpoint 的关联。`workflow_checkpoints` 仍然只是节点索引。

## LangGraph checkpoint

正式集成使用 `langgraph-checkpoint-postgres==3.2.1` 的 `PostgresSaver`。调用的是已安装源码里的 `PostgresSaver(connection)` 和 `setup()`。连接串由 `postgresql+psycopg://` 转成 `postgresql://`，`autocommit=True`。`setup()` 创建 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 和 `checkpoint_migrations`。

`ASE_CHECKPOINT_BACKEND=auto` 时，业务库是 PostgreSQL 就用这个 checkpointer，否则用 SQLite。本地单元测试继续走 SQLite。

State 只保留恢复工作流需要的标识和短结果：项目、需求、工作流、计划、审批、worktree、execution id、container id、测试是否通过、修复次数和评估摘要。模型消息和工具输出写在 `execution_events`，不写进 checkpoint。

## OpenHands Conversation

`openhands-sdk==1.63.1` 的 `RemoteConversation` 不允许设置 `persistence_dir`。会话在 Agent Server 上。传入已有 `conversation_id` 时，客户端先 `GET /api/conversations/{id}`，不存在才创建。

Agent Server 的默认 `conversations_path` 是相对路径 `workspace/conversations`。容器工作目录是 `/workspace`，所以会话文件在任务 worktree 的 `workspace/conversations/<id>/meta.json`。服务启动时从这里加载元数据。同一个 worktree 还在时，新容器可以重新看到会话。

`run()` 的代理循环在服务端。ASE 进程退出后，只要容器还在，循环就可以继续。正在飞的模型 HTTP 请求会随服务端中断而失败，不存在跨进程 exactly-once。ASE 用操作账本做可恢复的 at-least-once：先记下 conversation id，再执行；恢复时重新连接这个 id，不新建会话。
