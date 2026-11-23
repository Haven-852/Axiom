# Agent 生命周期

AgentRun 记录一次逻辑调用。Execution 记录实际副作用。两者的状态不能互相替代。

## AgentRun 状态

```text
pending -> running
pending -> cancelled
running -> completed | failed | interrupted | cancelled | timeout
interrupted -> running | failed | cancelled
```

`completed`、`failed`、`cancelled`、`timeout` 是终态。`assert_agent_transition` 拒绝其他跳转。

## 一次调用

1. Registry 按名称取出定义和工厂。
2. 校验输入 schema。
3. 写入 `pending` 的 AgentRun。相同 `idempotency_key` 直接返回已有记录，不再执行。
4. 检查取消，然后进入 `running`。
5. 在定义的 `timeout_seconds` 内执行 `agent.run`。
6. 校验输出 schema。输出里的 `workflow_id`、`agent_run_id`、`id`、`project_id` 会被丢掉，不能覆盖系统 ID。
7. 保存输出快照、Artifact、Execution 关联和真实的模型用量。
8. 写入终态。

超时把 AgentRun 标成 `timeout`，不标成 `completed`。已经由 ExecutionService 提交的 Execution 会留下。超时发生在 Agent 返回之前时，这次返回值里的 Artifact 不会再写一次。

未预期异常只保存异常类名。API Key、Authorization、password、token 会先脱敏。异常对象不入库。

## 与 Execution 的关系

| Agent | Execution |
| --- | --- |
| ResearchStub / Echo | 通常没有 |
| CodingAgent | 通过 `run_claimed` 关联一个或多个 |
| 后续 Testing | 可以关联已有 Execution |
| 后续 Diagnosis / Evaluation | 读取已有 Execution、事件和 TestResult |

同一个 Workflow 可以多次运行同一个 Agent。表上没有 `(workflow_id, agent_name)` 唯一约束。

LangGraph checkpoint 仍在 `checkpoints` 一组表。AgentRun 在 `agent_runs`。两边不合并。
