# Agent 编排

编排示例在 `src/ase/orchestration/`，不替换 `src/ase/workflow/graph.py`。

```text
START
  |
  v
research_stub
  |
  +-- errors 已记录 --> END
  |
  v
echo
  |
  v
END
```

节点是同步函数，内部用 `asyncio.run` 调用 Agent Runner。这样现有的 SqliteSaver 和 PostgresSaver 仍走同步 checkpoint 接口。

ResearchStub 的输入是 `{"requirement": ...}`。Echo 的输入是 `{"text": research_output["summary"]}`。传递的是结构化字段，不是聊天记录。

每个节点使用幂等键 `{workflow_id}:research_stub:1` 和 `{workflow_id}:echo:1`。同一次恢复不会再插入第二条 AgentRun。另一次调用要使用另一个幂等键。

节点失败时抛出 `AgentExecutionError`，不会静默进入下一个 Agent。`errors` 路由只在状态里已经有错误时结束。

Checkpoint 保存 `AgentWorkflowState`：workflow、project、requirement、当前 Agent、AgentRun id、Artifact、Execution、错误和两个 Agent 的输出。完整 OpenHands 事件不进入这份状态。

`interrupt_before=["echo"]` 可以在 ResearchStub 完成后停下，再用同一个 thread id 恢复 Echo。测试用 SqliteSaver 验证了这条路径。
