# Plan 审批

Plan 状态、Approval 状态和 Workflow 状态分开保存。

- Workflow：规划完成后保持 `planned`。`planned` 不是 `approved`。
- Plan 版本：不可变 Artifact。当前版本是最大版本号。
- Approval：`pending`、`approved`、`rejected`、`changes_requested`。计划审批的有效决定是后三种。

## 绑定

一条计划审批必须同时绑定 `workflow_id`、`plan_id`、`plan_version`、`plan_artifact_id` 和 `plan_hash`。subject 是 `plan-review:{plan_artifact_id}:{plan_hash}`。只绑定 Workflow 不能区分同一 Workflow 的不同版本。

comment 是 JSON，包含 actor、comment、计划标识、hash 和涉及的任务 id。actor 来自已通过的 API key，固定为 `api-key`。请求体不能指定另一个审批人。

## 操作

`POST /workflows/{id}/plan-approval`：

- `approved`：确认范围、DAG、依赖、验收标准和阻塞状态。
- `rejected`：必须给出原因。该版本不能执行，历史保留，可以再规划。
- `changes_requested`：必须给出意见。意见进入后续新版本，不修改已提交的版本。

批准前重新检查 Artifact 存在、hash 一致、Plan schema 和 DAG 有效、研究来源仍在、目标是当前版本，以及没有未解决的阻塞澄清。未解决或被延后的阻塞问题会使批准返回 409，并带上 `clarification_id`。本阶段没有“接受风险后跳过阻塞”的例外。

非阻塞且仍未回答的问题不阻止批准。已经被用户回答、但 Plan 正文和回答记录对不上的问题仍然阻止批准。因此只在旁边保存回答、却不生成新 Plan，不能批准 Version 1。

同一决定重复提交返回原审批。已经有终态后再提交另一种决定返回 409，不覆盖。本阶段不提供撤销。被新版本替代的旧版本不能批准。

批准成功后，只把同时位于拓扑序和 `execution_order` 的任务从 `blocked` 改为 `ready`。这仍然不是编码授权。服务不调用 `WorkflowService.resume`，不创建 Execution、Attempt 或 Conversation，也不启动 Docker。

旧接口 `POST /approvals/{id}/approve|reject` 如果遇到 `plan-review:` subject，返回 409，避免误恢复编码图。

## 与编码审批的关系

Migration `0004_phase23` 把唯一约束从 `workflow_id` 改为 `(workflow_id, subject)`。同一个 Workflow 以后可以同时有一条计划审批和一条编码审批。编码图仍使用自己的 subject 和 `set_approval`。

## 查询

`GET /workflows/{id}/plan-approvals` 只返回 subject 以 `plan-review:` 开头的记录。
