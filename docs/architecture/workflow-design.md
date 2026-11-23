# 工作流

`src/ase/workflow/graph.py` 中的 `StateGraph`：

```text
analyze -> plan -> validate
  -> request_approval -> human_approval
  -> workspace -> develop -> test
       -> evaluate -> deliver
       -> diagnose -> repair -> test
```

`human_approval` 调用 `langgraph.types.interrupt`。恢复使用同一个 `thread_id`（等于 workflow id）和 `Command(resume={"decision": ...})`。审批记录在进入中断节点之前写入 `approvals` 表。

检查点由 LangGraph checkpointer 保存。`workflow_checkpoints` 只记录 workflow id、thread id 和节点名，不替代业务表，也不保存对话事件。

测试失败且 `repair_count < max_repairs` 时进入诊断和修复。达到上限后仍生成评估和交付记录，状态为 `failed`。拒绝审批时工作流状态为 `cancelled`，不会创建 worktree。

节点通过 `ExecutionEvent.source` 区分 `ase`、`langgraph`、`openhands`、`docker`。
