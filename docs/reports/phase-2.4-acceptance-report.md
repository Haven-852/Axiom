# Phase 2.4 验收报告

日期：2026-11-23。阶段名称：Coding Agent Integration。

Phase 2.4 完成后停止。没有进入 Phase 2.5，也没有批准或执行 calculator 的 Version 2。

## 1. 实现内容

- Execution Eligibility Gate。
- 单任务 Scheduler。
- `CodingInput`、编码上下文和 `CodingResult`。
- CodingAgent 在带有计划字段时再次检查资格，然后调用现有 `ExecutionService.run_claimed`。
- 独立 LangGraph 线程 `{workflow_id}:coding:{task_id}`。
- Task Completion Gate。没有通过的 TestResult 和 Evaluation 时，任务不会变成 `completed`。
- API：查询资格、启动指定任务、按调度启动下一个任务。写操作要求 API key。

## 2. 架构变化

生产编码图没有删除节点，也没有被这条新线程替换。`planned` 不会自动进入编码。OpenHands SDK 仍然只在 `src/ase/adapters/openhands.py` 导入。

## 3. 数据库变化

没有新的 Migration。任务版本由 `plan_id` 关联。领取使用现有 `in_progress`。Execution、Attempt 和 AgentRun 关联表都没有新增。

## 4. 执行链路

资格检查、领取、Agent Runner、CodingAgent、`ExecutionService.run_claimed`、现有适配器、Execution 结果、完成门禁。

## 5. 资格门禁

未批准计划、阻塞澄清、未完成依赖都会在 Execution 创建前拒绝。`tests/test_phase24.py` 确认拒绝后 Execution 行数仍是 0。

calculator Workflow `1dd7c84d-36d1-4dcb-a66e-1004e0a1985f` 上，ready 任务 `8266a91d-8694-56d8-b615-7784e6436e09` 和 blocked 任务 `1d97ddce-d9dc-55b0-bace-69ee82c9f19e` 都返回 `plan_not_approved`。阻塞任务的错误包含 `c70c3a8e-4fcb-5fb1-8ed7-9efcd245a679`。该 Workflow 的 Execution 数没有增加，状态仍是 `planned`。`ase-poc-demo/calculator` 的文件哈希和 git status 没有变化。`ase.role=agent` 容器列表没有变化。

## 6. 调度

有依赖时，Scheduler 先选没有前置任务的文档任务，不会因为实现任务优先级更高而跳过依赖。

## 7. OpenHands 证据

`tests/test_phase24_openhands.py` 使用真实 OpenHands SDK 和 SDK 自带的 TestLLM，在隔离工作区创建了 `sdk_edit.txt`。Execution 保存了 Conversation id，状态是 `completed`。第二次启动复用了同一个 Execution。这不是真实模型验收。

另一次真实 DeepSeek 调用发生在 `var/phase24-real/workspaces/job`，没有使用 calculator 仓库。AgentRun `8594cefe-e9de-4c22-979f-f0bb6a5bd647`，Execution `349e8c88-c842-4496-a9fa-38e1abd712e4`。OpenHands 在 6 次迭代后达到上限，执行状态是 `error`。工作区没有新文件，分支仍是 `main`。任务被标成 `failed`，没有被标成 `completed`。这次真实模型验收没有通过。

## 8. Conversation

SDK 测试里的 Conversation id 写在 Execution 上。已完成的 operation 第二次不会再执行。真实模型那次没有形成可恢复的成功会话。

## 9. 测试

`tests/test_phase24.py`、`tests/test_phase24_calculator.py`、`tests/test_phase24_openhands.py` 和 Phase 2.0 的编码复用测试通过。

## 10. 未解决问题

真实 DeepSeek 编码没有在迭代上限内完成隔离仓库中的任务。calculator 的两个澄清 `c70c3a8e-4fcb-5fb1-8ed7-9efcd245a679` 和 `c7d1346e-5a7c-556e-9e9a-da556fa2ca3c` 仍未回答，Version 2 仍未批准。

## 11. 已知风险

生产图的旧入口仍然可以不经过 Plan 批准。新入口不会走那条路径。

SQLite 不执行 `FOR UPDATE`。任务级互斥在 PostgreSQL 上使用 advisory lock。

完成门禁依赖调用方写入真实 TestResult。CodingAgent 自己的摘要不能代替测试结果。

## 12. Phase 2.5 建议

先让真实模型在隔离仓库里完成一次编码、pytest 和 Conversation 恢复。不要在 calculator 的两个澄清被明确回答并生成新的已批准版本之前启动它的编码。
