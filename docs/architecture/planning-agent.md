# Planning Agent

Planning Agent 把已经持久化的 ResearchReport 变成一份结构化 Plan，并把任务 DAG 写入现有的 `development_plans`、`tasks` 和 `task_dependencies`。

LangGraph 仍是唯一的全局编排者。规划图只有一个 `planning` 节点，节点调用 Agent Runner，Runner 调用 `PlanningAgent`。模型调用走 `llm_client.complete()`。它不调用 OpenHands，不创建 Execution、Attempt 或 Conversation，不启动容器，也不读取项目文件。

## 输入

`load_planning_input` 只接受 Phase 2.1 的 Artifact 信封：

- `kind` 必须是 `research_report`
- `workflow_id` 必须属于当前 Workflow
- 正文必须包含 `content` 和 `agent_run_id`
- `content` 必须通过 ResearchReport schema
- 关联的 AgentRun 必须是同一个 Workflow 上的 `research`

缺 Artifact、信封不合法或报告校验失败时，不调用模型，不创建 Plan，也不创建 Task。

## 输出

模型返回 `PlanningDraft`。ASE 负责：

- 用规划指纹生成稳定的 `plan_id` 和 `task_id`
- 把研究报告里的每个澄清问题收成 `status=unresolved` 且 `blocking=true` 的 ClarificationItem
- 在存在未确认问题时，把 `implementation`、`testing`、`refactoring` 标为阻塞
- 本地计算拓扑序和 `execution_order`
- 用 DAG Validator 决定能否落库

`execution_order` 只包含可执行任务。被阻塞的任务留在 `topological_order` 里，不进入可执行顺序。

Plan 写入现有 `artifacts` 表，`kind` 为 `plan`，`uri` 为 `plan://{planning_agent_run_id}`。正文保存完整 Plan、研究 Artifact ID、研究 AgentRun ID、规划 AgentRun ID、任务 ID、阻塞任务 ID 和校验结果。不复制整份 ResearchReport。

## 幂等

指纹包含：

- workflow_id
- research_artifact_id
- research_report_hash
- agent name `planning` 和 version `1`
- format repair 上限
- prompt_version

同一个指纹的 `completed` 记录直接复用，不再调用模型，也不再插入任务。报告、Agent 版本或 prompt_version 变化时允许新的 AgentRun。失败记录保持失败，不会被当成成功。这是 at-least-once。

## 事务

校验失败时 Agent 返回失败，Runner 不写 Plan Artifact。校验通过后，Task DAG 在 Agent 返回前的一个数据库事务里写入。Runner 随后单独提交 Artifact 和完成状态。进程若在这两步之间退出，恢复时用同一主键跳过已有任务，再补写 Artifact。

## 工作流

规划图的 thread id 是 `{workflow_id}:planning`，不覆盖研究图的 checkpoint。成功后把 Workflow 标为 `planned`。`planned` 和 `researched` 一样不占用项目活动名额。失败不把已经是 `researched` 的 Workflow 改成 `failed`。

本阶段不把规划接入生产编码图。

## API

- `POST /api/v1/workflows/{workflow_id}/planning`
- `GET /api/v1/workflows/{workflow_id}/plan`
- `GET /api/v1/workflows/{workflow_id}/tasks`

API 只校验请求并调用 PlanningWorkflowService。它不直接调用模型客户端。
