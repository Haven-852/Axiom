# 可观测性

执行事件仍然按时间顺序存在 `execution_events`。在此之外记录能够从真实结果得到的指标。

模型用量来自 OpenHands 的 conversation stats。能读到 `prompt_tokens` 或 `completion_tokens` 时写入 `model_usages`，包括 SDK 返回的 0 费用。读不到就不填，也不估算价格表。查询入口是 `GET /api/v1/usage`，可以按 project、workflow、task 和 model 过滤。

测试事件保存命令、退出码、耗时，以及输出里真实出现的 passed 和 failed 计数。没有匹配到计数时这两个字段为空。

评估事件额外保存观察到的修复次数、diff 字符数和测试是否通过。这不是质量评分。

工作流最终状态、审批决定和容器 inspect 结果沿用已有事件。密钥在写入前脱敏。
