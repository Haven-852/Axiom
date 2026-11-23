# ADR-003 LangGraph 工作流

状态：已接受。

主流程使用 `StateGraph`。人工审批使用 `interrupt` 和 `Command(resume=...)`，thread id 等于 workflow id。恢复的是原来的检查点，不是新建一张图。

图状态是 `TypedDict`。需求分析、计划和评估在节点里用 Pydantic 模型校验后再写入业务表。这样检查点里只有 JSON 兼容数据，领域模型也不被 LangGraph 的 reducer 改写。

测试失败的条件边进入诊断和修复。修复次数由 `ASE_MAX_REPAIR_ATTEMPTS` 限制。
