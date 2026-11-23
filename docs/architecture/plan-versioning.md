# Plan 版本

已经写出的 Plan Artifact 不修改。版本关系记在另一条 Artifact 上。

## 记录

`kind=plan_version`。第一次读取某个 Workflow 的计划时，如果还没有版本记录，就用最早的 `kind=plan` Artifact 登记 Version 1。登记不改写该 Artifact 的 body。

后续版本的 URI 是 `plan-version://{workflow_id}/{parent_plan_artifact_id}/{fingerprint}`。版本号是已有版本数量加 1，分配发生在 Workflow 行锁内。

每条记录包含：

- `plan_id`
- `plan_version`
- `parent_plan_artifact_id`
- `source_research_artifact_id`
- `source_research_agent_run_id`
- `planning_agent_run_id`
- `plan_artifact_id`
- `plan_hash`
- `clarification_revision`
- `origin`：`initial`、`planning` 或 `edit`
- `created_at`

当前版本是最大的 `plan_version`。审批状态不写回版本 Artifact，而是由对应的 Approval 行得出。新版本不复制旧版本的 `approved`。

## Hash

`plan_hash` 是完整 Plan JSON 的 SHA-256，包含 `validation_result`，`sort_keys=True`，`ensure_ascii=False`。批准时客户端必须提交同一个 hash。不一致返回 409。

## 重新规划

用户至少回答了一个问题后，`POST /workflows/{id}/plan-revision` 复用 `PlanningWorkflowService` 和 `PlanningAgent`。输入是原始 ResearchReport、上一版 Plan、用户决策、延后问题、上一版任务和当前版本。ResearchReport 不被修改。用户决策优先于研究报告里的推断，但原始推断仍留在研究报告中。

模型输出之后，ASE 再按用户决策改写澄清状态：已回答的问题写成 `answered` 并保存回答原文；未回答的保持 `unresolved`；用户延后的写成 `deferred`。模型不能把没有出现在决策列表里的问题标成已回答。

指纹由已回答的问题文本、回答文本和延后问题文本组成，不包含会随版本变化的 clarification id。当前版本的 `origin=planning` 且指纹相同，就直接返回该版本，不再调用模型，也不再插入任务。

新版本使用新的 `plan_id` 和新的任务 id。旧任务行不更新。标题仍在、模型没有再输出的任务会被保留，并带上“没有删除理由”的说明。保留任务的依赖会映射到新 id。Version 2 的真实结果是在这个映射补上之前生成的，所以那一版被保留任务的依赖列表是空的；该版本不再改写。

## Diff

`GET /workflows/{id}/plan-diff?left=&right=` 按任务标题比较描述、优先级、类型、验收标准、相关文件和依赖标题。澄清按问题文本和状态比较。任务 id 在版本之间会变，所以不用 id 对齐。

## 人工编辑

`POST /workflows/{id}/plan-edits` 不调用模型。每次编辑要求原因，生成新版本，重新运行 DAG Validator，并把实现类任务保持为待批准的 `blocked`。已批准的历史版本不被修改。编辑和澄清回答是两类审计记录。

## 幂等与事务

版本登记、回答写入和审批都在 PostgreSQL 事务里对 Workflow 行 `SELECT ... FOR UPDATE`。SQLite 会忽略这个锁，所以并发测试放在 PostgreSQL。模型调用在锁外完成；返回后再加锁，URI 已存在就复用。语义是 at-least-once，不是 exactly-once。
