# ADR-011 AgentRun 领域

状态：已接受。

Agent 的逻辑调用和实际执行分开记录。

`AgentRun` 保存一次 Agent 调用：workflow、可选 task、名称、版本、状态、输入输出快照、Artifact id、Execution id 和幂等键。`agent_run_executions` 表达可空的多对多关联。没有新建 Attempt 表，attempt 继续是 `execution_operations`。

`AgentRunResult` 保持为 Coding Adapter 的返回值，不改名，也不当作 AgentRun。

生产编码节点仍直接调用 Coding Adapter。Agent 层的 CodingAgent 通过 ExecutionService 进入同一端口，避免重写已通过的 Conversation 恢复。

LangGraph checkpoint 与 `agent_runs` 分表。
