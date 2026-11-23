# Phase 2.2 验收报告

日期：2026-11-23。阶段名称：Planning Agent Implementation。

Phase 2.2 完成后停止。没有实现 CodingAgent，没有修改 OpenHands Adapter，也没有把规划接入生产编码图。

## 1. 实现内容

- `PlanningAgent`，名称 `planning`，版本 `1`。
- Plan、PlanTask、ClarificationItem、PlanRisk 和 PlanValidationResult schema，版本 `1.0`。
- Research Artifact 信封读取和校验。
- 本地 DAG Validator 和 Task Builder。
- 独立规划图、PlanningWorkflowService，以及查询 Plan 和 Task 的 API。
- 计划 Artifact，`kind=plan`。

## 2. 架构变化

LangGraph 仍是唯一编排者。新增的规划图不替换 `src/ase/workflow/graph.py`。规划不获取仓库写锁，不调用 ProjectReader，不创建 Execution。

Workflow 增加终态 `planned`。`planned` 不占用项目的活动名额，取消操作也不会把它改写成 `cancelled`。

## 3. 数据库变化

没有新的 Migration。Alembic head 仍是 `0003_phase20`。

任务 DAG 使用已有的 `development_plans`、`tasks` 和 `task_dependencies`。追溯字段留在 Plan Artifact。`tasks.agent_id` 保存 `investigation`、`documentation`、`implementation` 或 `testing`，不保存 `coding`。

## 4. 测试

非真实模型回归：57 通过，退出码 0。范围排除了 `test_faults.py`、`test_phase121_live.py`、`test_real_poc.py`、`test_real_repair.py`、`test_openhands_resume.py`、`test_phase21_live.py` 和 `test_phase22_live.py`。

PostgreSQL 测试覆盖了脚本化规划写入，以及一条悬空依赖在外键失败后回滚。

真实 DeepSeek 规划测试 `tests/test_phase22_live.py` 通过。这是 Level 1 证据。脚本化测试不是模型验收。

## 5. 真实模型证据

消费的是 Phase 2.1 已有研究报告，没有重新执行 ResearchAgent。

- Workflow：`1dd7c84d-36d1-4dcb-a66e-1004e0a1985f`
- Research Artifact：`450e2050-cd34-4dcc-8766-fec43e63fc82`
- Research AgentRun：`c36ae47a-eaf4-43d7-8c1d-d11af093eb9e`
- Planning AgentRun：`0fbc7053-40db-478e-83b8-66d9cc15394d`
- Plan Artifact：`b5b35a61-01a0-4971-a6b3-3e0c8587bcbc`
- Plan：`06e3507e-7263-5e77-98b0-4769b732cca5`
- Provider：`deepseek`
- Model：`deepseek-flash`
- Prompt tokens：3727
- Completion tokens：7662
- Total tokens：11389
- Latency：39.545 秒
- Estimated cost：`null`

这一行 ModelUsage 是该 AgentRun 内实际调用的合计。completion tokens 高于单次 `max_tokens` 4096，所以这次成功运行包含格式修复调用。没有按次拆开的用量行，因此不拆分、不估算费用。

同一 Workflow 上还有两次未采用的失败尝试：

- `6e69b25d-9027-4190-99c2-d94677f828fe`：模型把复杂度写成 `low`，schema 未通过。这次发生在失败也记录用量之前，没有 ModelUsage 行。
- `4acbb2cb-c93d-4943-b038-23666e67e5c9`：模型 JSON 在第 5944 个字符处无法解析。记录到的合计是 prompt 6034、completion 10873、total 16907、latency 54.415 秒，estimated cost 仍是 `null`。

失败记录没有被复用为成功。prompt_version 变化后才允许新的规划。随后用同一指纹再次启动，没有新增 Task，也没有新增 ModelUsage。

DAG 校验 `valid=true`。任务 4 个，阻塞 2 个，可执行 2 个，澄清项 7 个。研究报告修订 `54a0303761a57d1e572c8304881975134b3b594a`。

`ase-poc-demo/calculator` 的文件哈希和该路径的 git status 在运行前后一致。该 Workflow 的 Execution 数量为 0，带 Conversation 的 Execution 数量为 0，`ase.role=agent` 的容器数量为 0。

验收后该 Workflow 状态是 `planned`。它在 Phase 2.1 结束时仍是 `running`，因为当时研究图是直接调用的。

## 6. Task DAG 示例

可执行顺序：

1. `0b7728bd-eb9d-52d9-906a-6dae306c9a85`，investigation，`ready`：调查现有计算器行为。
2. `9b3f8533-94b1-5d03-9b68-12701096faf2`，documentation，`ready`：记录现状与待确认项。依赖第 1 项。

阻塞任务：

3. `37c9f4cb-316e-5a98-9b14-11fd13004cc1`，implementation，`blocked`：实现科学计数法支持。依赖第 1 项。
4. `0a155145-5a1d-597c-90bb-5bbeab68c0c5`，testing，`blocked`：补充科学计数法测试。依赖第 1 项和第 3 项。

7 个澄清项全部是 `unresolved` 且 `blocking=true`。其中 5 个来自 ResearchReport，包括“只支持数值字面量，还是包含运算符和括号的完整表达式”。模型另外产生了 2 个相近问题，也保持未回答。计划和任务文本没有把字面量或完整表达式写成已确认需求。

## 7. 未解决问题

- 科学计数法是字面量还是完整表达式。
- 是否支持大写 E、正负指数、空白和多个数值。
- 非法输入抛出 ValueError、返回错误值，还是其他行为。
- 现有 NotImplementedError 测试应删除还是改写成成功用例。
- 使用 float 还是高精度十进制。

## 8. 已知风险

- `ready` 的调查和记录任务不能被编码图自动执行。当前编码图不会按 `tasks.status` 领取这些行，后续阶段也不能仅凭 `ready` 启动 OpenHands。
- 模型用量按 AgentRun 合计。失败尝试和格式修复都会产生真实调用，但不是每次 HTTP 请求一行。
- 任务表不保存验收标准和 finding。追溯必须读 Plan Artifact。
- 规划成功和 Artifact 完成不是同一个数据库事务。崩溃恢复靠确定性主键补写，仍然是 at-least-once。

## 9. Phase 2.3 建议

建议先做澄清确认，再让 Coding Agent 只领取未被阻塞、且类型明确为实现的任务。确认之前不要解除 implementation 和 testing 的阻塞，也不要由编码代理替用户选择字面量或完整表达式。

Coding Agent 仍应通过现有 Execution 路径修改仓库。不要在本阶段的 `ready` 任务上直接调用 OpenHands。
