# ADR-005 人工审批

状态：已接受。

计划审批是 LangGraph 中断。工具确认是 OpenHands 的 confirmation policy，本阶段不把这两层合成一个接口。

`request_approval` 先把 `Approval` 写入数据库，随后 `human_approval` 中断。`POST /api/v1/approvals/{id}/approve` 和 `reject` 需要 `X-ASE-API-KEY`，然后用原来的 workflow id 恢复。

审批通过只允许在任务分支上提交。合并到主分支不是这个审批的结果，`merge_to_main` 会拒绝。
