# Phase 2.3 验收报告

日期：2026-11-23。阶段名称：Human-in-the-loop Plan Approval and Clarification Resolution。

Phase 2.3 完成后停止。没有实现 CodingAgent，没有修改 OpenHands Adapter，没有修改生产编码图，也没有进入 Phase 2.4。

## 1. 实现内容

- `ClarificationItem` 增加回答、回答人、回答时间和来源引用。历史回答不写回旧 Plan。
- `PlanReviewService`：列出澄清、保存回答和延后、计算阻塞、重新规划、人工编辑、比较版本、批准、拒绝、要求修改。
- Plan 版本 Artifact，`kind=plan_version`。Version 1 由已有 Plan Artifact 登记，不改其 body。
- 回答和延后 Artifact。相同文本幂等，不同文本追加 revision。
- 审批复用 `approvals`。subject 绑定 Plan Artifact 和 Plan Hash。新增状态 `changes_requested`。
- API 写操作要求现有 API key。actor 固定为 `api-key`。
- 旧审批接口遇到 `plan-review:` 返回 409，不恢复编码图。

## 2. 架构变化

LangGraph 仍是唯一编排者。重新规划复用已有 Planning Workflow，不新增第二套工作流引擎。

Plan 版本、Approval 和 Workflow 状态分开。Workflow 成功规划后仍是 `planned`。`planned` 不是 `approved`。`ready` 不是执行许可。批准只更新当前版本里已经通过 DAG 的任务状态，不调用 `WorkflowService.resume`。

## 3. 数据库变化

Alembic head 从 `0003_phase20` 变为 `0004_phase23`。

Migration 删除 `uq_approvals_workflow`，新增 `uq_approvals_workflow_subject (workflow_id, subject)`。没有新表。版本、回答和延后都写在已有 `artifacts` 表。

## 4. Plan Version 示例

Workflow `1dd7c84d-36d1-4dcb-a66e-1004e0a1985f`。

Version 1：

- plan_id `06e3507e-7263-5e77-98b0-4769b732cca5`
- plan_artifact_id `b5b35a61-01a0-4971-a6b3-3e0c8587bcbc`
- planning_agent_run_id `0fbc7053-40db-478e-83b8-66d9cc15394d`
- plan_hash `23118dd2c1d1a48ca8a991f3b2164222ef4813144b1d1438a1e85a6d7cd9c4ad`
- source_research_artifact_id `450e2050-cd34-4dcc-8766-fec43e63fc82`
- origin `initial`

Version 2：

- plan_id `cbd9adf8-1dca-561e-8509-b6cd1cf316ab`
- plan_artifact_id `18312712-5015-4eed-9e14-3d609bdb09b7`
- planning_agent_run_id `c5c6a1e7-0a2a-4054-aa5e-34d7f2312f32`
- plan_hash `a3aabaa1c34d9e53d0321ce7dc78ca821747109b57cd34c709e79b446b4b0914`
- parent_plan_artifact_id `b5b35a61-01a0-4971-a6b3-3e0c8587bcbc`
- source_research_artifact_id `450e2050-cd34-4dcc-8766-fec43e63fc82`
- origin `planning`

Version 1 的 Artifact body 在回答和重新规划之后保持原字节。Version 2 没有继承批准状态。

## 5. Clarification 回答示例

原问题 `3ec1c56a-94a6-528e-b3d7-c06973cb8cd9`：非法输入应抛出 ValueError、返回错误值，还是其他行为？

- answer：`非法输入是抛出 ValueError、返回错误值`
- answered_by：`api-key`
- answered_at：`2026-11-23T07:53:22.069322+00:00`
- plan_version：`1`
- answer_revision：`1`
- state：`answered`

同一句同时保留“抛出 ValueError”和“返回错误值”，没有改成其中一种。另外五条已回答记录是：

- `18e72a71-dd9c-5a95-a037-eff6ec695c34`：完整数学表达式
- `fd0213b5-695c-5cb1-9f44-5008c1bc8fe5`：支持大写 E、正负指数、空白和多个数值
- `f7aa6e10-b062-5148-8984-4c208f762596`：现有 NotImplementedError 测试应删除
- `6dc3e475-14fc-5a66-a161-0626401ab9cc`：使用 float
- `0a8bcf8c-f630-5714-af9c-2b03bb6ba7de`：完整数学表达式

`c70c3a8e-4fcb-5fb1-8ed7-9efcd245a679`（科学计数法输出格式、精度与异常行为应如何定义？）没有回答，仍是 `unresolved` 且 `blocking`。

## 6. Approval 示例

真实计算器计划没有批准记录。对 Version 2 调用批准返回 409，原因包含仍未解决的 `c70c3a8e-4fcb-5fb1-8ed7-9efcd245a679`。Workflow 上的 `approvals` 行数仍是 0。

脚本化 PostgreSQL 测试在另一个 Workflow 上写入了 `approved`，并用新的数据库会话读回同一行。重复批准返回原行。批准后再拒绝返回 409。`changes_requested` 和 hash 不匹配也在 SQLite 测试中覆盖。旧的 approve 接口遇到计划审批返回 409，Workflow 仍是 `planned`，Execution 数是 0。

并发测试里两个决定同时提交，只留下一行，另一个得到 `WorkflowConflict`。错误 hash 不插入审批行。

## 7. Task DAG

Version 1 的四行没有变化：

| 任务 | 类型 | 状态 |
|---|---|---|
| `0b7728bd-eb9d-52d9-906a-6dae306c9a85` | investigation | ready |
| `9b3f8533-94b1-5d03-9b68-12701096faf2` | documentation | ready |
| `37c9f4cb-316e-5a98-9b14-11fd13004cc1` | implementation | blocked |
| `0a155145-5a1d-597c-90bb-5bbeab68c0c5` | testing | blocked |

Version 2 校验通过。`executable_task_count=2`，`blocked_task_count=6`。数据库状态：

| 任务 | 类型 | 状态 |
|---|---|---|
| `8266a91d-8694-56d8-b615-7784e6436e09` 调查现有计算器行为 | investigation | ready |
| `3147eb8b-93dd-5ae2-884c-5a7b1085cdee` 记录现状与待确认项 | documentation | ready |
| `1d97ddce-d9dc-55b0-bace-69ee82c9f19e` 实现科学计数法表达式求值 | implementation | blocked |
| `df71a45d-b40b-556e-b8dd-c58c2995be08` 更新正向测试并移除桩测试 | testing | blocked |
| `9271054a-40da-5955-810f-3e5fb9ba735f` 补充非法输入测试 | testing | blocked |
| `78c82c2d-b902-5dce-b02a-03f5ca1a31fe` 更新 README 语法说明 | documentation | blocked |
| `000a313b-2928-5c96-b1fa-0ee039312a8b` 实现科学计数法支持 | implementation | blocked |
| `fefbd975-8a04-5992-97bf-f3bd847db932` 补充科学计数法测试 | testing | blocked |

后两行是从 Version 1 保留的任务，使用新 id。旧行没有被改写。执行顺序只有两条 ready 任务。实现和测试仍被未回答问题挡住。没有任务的 `agent_id` 是 `coding`。

## 8. 测试

确定性测试：

- `tests/test_phase23.py`：3 通过。覆盖默认 unresolved、空回答、未知 id、回答历史、延后、版本不覆盖、重复规划复用、部分回答不能批准、批准后拒绝不能覆盖、编辑产生新版本、保留任务的依赖，以及计划审批不恢复编码图。
- `tests/test_phase23_postgres.py`：2 通过。覆盖新唯一约束、两个 subject、新会话读回批准和回答、错误 hash 回滚、并发矛盾决定。

非真实模型回归排除 `test_faults.py`、`test_phase121_live.py`、`test_real_poc.py`、`test_real_repair.py`、`test_openhands_resume.py`、`test_phase21_live.py`、`test_phase22_live.py` 和 `test_phase23_live.py`。62 项里 61 项通过。`test_agent_container_limits_files_and_pytest` 第一次因容器内安装 pytest 时的 SSL EOF 失败，单独重试通过。这不是本阶段逻辑失败。

`tests/test_phase22.py` 仍通过。Phase 2.2 的规划幂等键没有改。

## 9. PostgreSQL 与真实模型证据

真实重新规划只调用了一次 DeepSeek。没有重新执行 ResearchAgent。脚本化测试不是这次模型验收。

- provider `deepseek`，model `deepseek-flash`
- AgentRun `c5c6a1e7-0a2a-4054-aa5e-34d7f2312f32`
- prompt 6233，completion 6398，total 12631，latency 32.535 秒，estimated_cost `null`
- 第二次 revision 没有新增 ModelUsage，也没有新增 DevelopmentPlan

Phase 2.2 的两条用量仍在，没有改写：失败的 `4acbb2cb-c93d-4943-b038-23666e67e5c9`，成功的 `0fbc7053-40db-478e-83b8-66d9cc15394d`。第一次失败规划 `6e69b25d-9027-4190-99c2-d94677f828fe` 仍然没有用量行。

新的数据库会话能读到 Version 2 和六条回答。Workflow 状态仍是 `planned`。该 Workflow 的 Execution 数是 0，带 conversation id 的 Execution 数是 0。`ase.role=agent` 容器列表与验收前相同。`ase-poc-demo/calculator` 的文件哈希和 `git status --porcelain` 与验收前相同。

## 10. 未解决问题

输出格式没有用户回答，`c70c3a8e-4fcb-5fb1-8ed7-9efcd245a679` 继续阻塞。

模型另外生成了一条未回答的阻塞问题 `c7d1346e-5a7c-556e-9e9a-da556fa2ca3c`：科学计数法非法输入应抛出 ValueError 还是返回错误值？用户决策中两种行为同时出现且未确认，请明确。原问题已经按用户原句保存。服务没有把这两条合并，也没有替用户选择一种行为。

在这两条关闭之前，这个 Plan 不能批准。

## 11. 已知风险

- Version 2 里被保留的旧任务依赖列表是空的。依赖映射是在这次真实调用之后补上的，已生成的版本不再改写。后续修订会保留映射后的依赖。脚本化测试覆盖了这条修复。
- 系统是单用户。actor 只有 `api-key`，没有 RBAC。
- 持久化是 at-least-once。相同指纹会复用，不能称为 exactly-once。
- 本阶段不能撤销已批准的计划。矛盾决定会被拒绝，不会覆盖。
- 模型可能把一句同时包含两种行为的回答再写成新的阻塞问题。服务不会自动回答它。

## 12. Phase 2.4 建议

先由用户明确回答剩余的输出格式问题，以及模型新增的非法输入确认问题。不要把这两条未决问题当成已经同意。

Phase 2.4 如果实现 CodingAgent，只能消费已经 `approved` 的当前 Plan 版本。不能把 `ready` 或 `planned` 当作执行授权，也不能在批准时恢复编码图。本阶段不开始这项工作。
