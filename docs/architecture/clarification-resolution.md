# 澄清事项

澄清事项记录一个还没有由用户决定的问题。它不是模型可以自行关闭的待办。

## 生命周期

新问题的状态是 `unresolved`。只有用户提交了非空回答，状态才变成 `answered`。用户也可以 `defer`。`deferred` 不是 `answered`，阻塞问题继续阻塞受影响任务。

服务不会根据 PlanningAgent 的推断、用户的沉默，或两个问题措辞相近，自动回答或自动合并。展示层如果要合并，必须同时保留每一条原始 `clarification_id` 和来源。本阶段的服务不做合并。

## 来源

Plan 里的 `ClarificationItem.source` 保留生成时的来源。Phase 2.2 的研究报告问题使用 `research_report`。模型补充的问题使用 `planning`。用户回答记录的 `source` 是 `user`。

`source_reference` 指向研究报告中的原问题、上一版澄清 id，或用户补充说明。历史 Plan Artifact 不被改写。

## 回答记录

回答和延后都是不可变 Artifact：

- `kind=clarification_answer`
- `kind=clarification_deferral`

URI 是 `{kind}://{plan_id}/{clarification_id}/{sha256(text)[:16]}`。同一计划、同一问题和同一文本再次提交时返回原记录。不同文本生成新记录，`answer_revision` 递增。旧记录不更新。

每条记录包含 `clarification_id`、原问题、回答或延后原因、`answered_by`、`answered_at`、`plan_id`、`plan_version`、`source`、`source_reference`、`answer_revision` 和受影响任务 id。

写操作经过 `PlanReviewService`。API 不直接写表，也不调用模型。

## 查询

`GET /workflows/{id}/clarifications` 读取当前 Plan 的问题，并用该问题文本的最新记录覆盖状态。计划正文本身保持不变。

`GET /workflows/{id}/clarifications/{cid}/history` 返回这个 id 的记录，以及问题文本完全相同的历史记录。这样新版本里的新 id 仍能看到上一版的回答。文本不同的相似问题不会被带出来。

## 阻塞

`blocking=true` 且状态不是 `answered` 的问题继续阻塞。一个任务上只要还剩一个阻塞问题，任务就保持 `blocked`。非阻塞问题可以留着不回答。

回答本身不修改任务行，也不解除阻塞。解除还要求新 Plan 已经生成、schema 和 DAG 通过、任务关联已经写入，并且这个 Plan 版本已经被人工批准。

## 身份

系统目前是单用户。写接口要求 `X-ASE-API-Key`。actor 固定为 `api-key`，不采用请求体里的 `answered_by`。没有多用户 RBAC。
