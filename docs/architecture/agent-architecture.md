# Agent 架构

Phase 2.0 把智能体的逻辑调用从实际执行里拆出来。LangGraph 仍是唯一的全局编排者。OpenHands 只作为 Coding Runtime。

```text
LangGraph
  |
  +-- 生产工作流 graph.py
  |     analyze / plan / approval / develop / test / diagnose / repair / evaluate / deliver
  |     develop 继续调用 CodingAgentAdapter，不经过新的 Agent Runner
  |
  +-- 编排示例 orchestration/
        Agent Runner -> ResearchStubAgent -> EchoAgent

Agent Runner
  |
  +-- AgentRegistry
  +-- AgentRun（PostgreSQL）
  +-- CodingAgent
        |
        v
      ExecutionService
        |
        v
      CodingAgentAdapter
        |
        +-- ScriptedCodingAdapter
        +-- OpenHandsCodingAdapter
              |
              v
            Docker Agent Server
```

生产图里的 `_run_coding` 没有改到 ExecutionService。已验证的 Conversation 恢复、崩溃点和操作跳过仍留在原函数。CodingAgent 是 Agent 层的新入口，测试用 ScriptedCodingAdapter 证明它复用同一套 operation 和 execution。

Research、Planning、Diagnosis、Evaluation 不需要接入 OpenHands。本阶段只实现了 ResearchStubAgent 和 EchoAgent，用来验证协议。它们不是正式的 Research Agent。

AgentRun 与 Execution 不是一对一。关联表是 `agent_run_executions`。没有 Execution 的 AgentRun 可以独立完成。
