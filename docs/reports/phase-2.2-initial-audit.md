# Phase 2.2 初始审计

日期：2026-11-23。审计在实现 Planning Agent 之前完成。最新 Migration 是 `0003_phase20`。

## 已有能力

- ResearchAgent 在 `src/ase/agents/research/`。报告协议在 `schemas.py`，版本 `1.0`。
- 真实 Research Artifact `450e2050-cd34-4dcc-8766-fec43e63fc82` 的正文是信封，不是裸报告。键为 `schema_version`、`agent_run_id`、`workflow_id`、`project_id`、`revision`、`files_read`、`search_results`、`content`。`content` 才是 ResearchReport。
- 该报告有 5 个 `clarification_questions`，其中包括科学计数法是字面量还是完整表达式。finding id 为 `CF-001` 到 `CF-004`。验收标准 id 为 `AC-001` 到 `AC-008`，其中 `AC-006` 起是 `inferred`。
- 对应 Workflow `1dd7c84d-36d1-4dcb-a66e-1004e0a1985f` 当前状态仍是 `running`。研究图是直接调用的，没有走 `ResearchWorkflowService`，所以没有写成 `researched`。
- Agent Runner 已能按幂等键复用 `completed`，失败和超时不会当成成功。Artifact 按 uri 去重。ModelUsage 只在真实 token 为整数时写入。
- `llm_client.complete()` 已支持 JSON object 模式。

## Task DAG

已经存在，不需要第二套任务系统。

- `development_plans` 通过 `requirement_id` 关联需求。Workflow 也有 `requirement_id`。
- `tasks` 有 `plan_id`、`title`、`instruction`、`status`、`agent_id`。
- `task_dependencies` 用 predecessor 和 successor 表达有向边。
- `TaskStatus.BLOCKED` 已存在。阻塞不需要靠改写描述伪造。

`tasks` 没有优先级、任务类型、验收标准、文件和 finding id 列。这些追溯信息放在 `kind=plan` 的 Artifact 正文里，任务行只承担 DAG 和阻塞状态。`agent_id` 保存任务类型，避免占用 `coding`，这样生产编码图不会把规划任务当成待执行编码任务。

不需要新 Migration。重复写入靠确定性主键：同一个规划指纹得到同一个 `plan_id` 和 `task_id`。已存在则跳过。

## 事务边界

Agent Runner 在 Agent 返回成功之后才写 Artifact 和完成状态，这两步不在同一个事务里。Task 写入放在 PlanningAgent 返回之前的一个 `session_scope` 中。校验失败不写任务、不返回成功，Runner 也就不会写 Plan Artifact。

若任务已提交而进程在 Runner 完成前退出，恢复时用同一主键跳过任务，再补写 Artifact。这是 at-least-once，不是 exactly-once。

## 可复用

Agent Runner、Registry、AgentRun、Artifact、ModelUsage、`complete()`、checkpoint、`save_plan`、`TaskStatus.BLOCKED`、`TaskDependency`。

## 新增

- `src/ase/planning/`：Schema、研究报告读取、DAG 校验、Task 构建、规划服务。
- `src/ase/agents/planning_agent.py`。
- 独立规划图。thread id 使用 `{workflow_id}:planning`，避免覆盖 Phase 2.1 的研究 checkpoint。

## 修改

- Workflow 增加终态 `planned`，使规划完成后不再占用项目活动名额。
- Store 增加按 plan 读取任务和依赖，以及按 workflow 列出 Artifact。
- API 增加在已有 workflow 上启动规划，以及查询计划和任务。

不修改 `src/ase/adapters/openhands.py`，不修改生产编码节点。

## 风险

- 研究报告的澄清问题会阻塞 implementation 和 testing。不依赖该决策的 documentation 或 investigation 可以保持可执行。
- 模型若把未确认范围写成已确认需求，校验会失败，不会落成任务。
- 规划 Workflow 和编码 Workflow 不是同一张图。本阶段不自动进入编码。

实现后记：上述方案已落地，没有新增 Migration。真实验收之后，Workflow `1dd7c84d-36d1-4dcb-a66e-1004e0a1985f` 的状态是 `planned`。验收数字见 `phase-2.2-acceptance-report.md`。
