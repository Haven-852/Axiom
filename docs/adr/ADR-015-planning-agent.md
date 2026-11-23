# ADR-015 Planning Agent

状态：已接受。

Planning Agent 消费已有 ResearchReport，生成 Plan 和 Task DAG。它通过现有 Agent Runner 执行，模型调用使用 `llm_client.complete()`。OpenHands 仍然只属于编码运行时。

任务依赖继续使用 `development_plans`、`tasks` 和 `task_dependencies`。不新增任务表、AgentRun 表、Artifact 表或 ModelUsage 表，也不增加 Migration。任务类型写入 `tasks.agent_id`，阻塞使用已有的 `blocked` 状态。验收标准、finding 和澄清关系保存在 `kind=plan` 的 Artifact 中。

未确认的澄清问题保持 `unresolved`。会改变实现范围的问题阻塞 implementation、testing 和 refactoring。规划图使用独立 thread id `{workflow_id}:planning`。完成后 Workflow 状态为 `planned`，不进入编码图，不创建 Execution。

同一个规划指纹的成功结果直接复用。语义仍是 at-least-once。
