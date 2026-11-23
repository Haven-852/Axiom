# ADR-008 Conversation 持久化

状态：已接受。

OpenHands 会话不写入 LangGraph checkpoint。Agent Server 把会话放在 worktree 的 `workspace/conversations`。ASE 只在 Execution 和 `execution_operations` 里保存 conversation id。

恢复时把这个 id 交给 `RemoteConversation`。SDK 会先读取已有会话。已有用户消息时不再发送同一条任务。Agent Server 进程本身重启后，只要这个目录还在，服务端可以重新加载元数据。容器被删除且 worktree 被删掉时，会话无法恢复，ASE 必须记为失败而不是另开一个会话冒充。
