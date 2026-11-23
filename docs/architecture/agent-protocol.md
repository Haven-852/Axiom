# Agent 协议

## AgentDefinition

静态定义沿用 `AgentSpec`，并补上 `version`、`capabilities`、`enabled`、`max_retries`。定义里没有 API Key。`input_schema` 和 `output_schema` 是可查询的 JSON Schema 子集。

校验支持 `type`、`required`、`properties`。布尔值不会被当成整数。

## AgentContext

只带当前调用需要的标识：

- `workflow_id`
- `agent_run_id`
- `project_id`
- `task_id`
- `correlation_id`
- `input_artifact_ids`
- `execution_ids`
- `metadata`

取消通过 `bind_cancellation` 挂上，不进序列化字段。`is_cancelled` 看本地请求标志，也看 Workflow 是否已经是 `cancelled`。`raise_if_cancelled` 抛出 `AgentCancelledError`。

Context 不装入历史聊天、全部 ExecutionEvent 或整库数据。

## AgentResult

Pydantic V2 模型。`status` 使用 `AgentRunStatus`。时间使用带时区的 datetime。字段包括 agent 名称和版本、workflow、agent run、输出字典、输入输出 Artifact、Execution 列表、错误码和错误文本。

## 异常

| 异常 | code |
| --- | --- |
| InputValidationError | input_invalid |
| AgentNotFoundError | agent_not_found |
| AgentExecutionError | agent_execution_failed |
| AgentTimeoutError | agent_timeout |
| AgentOutputValidationError | output_invalid |
| AgentCancelledError | agent_cancelled |
| AgentAlreadyRegistered | agent_already_registered |

## 模型用量

只在 `prompt_tokens` 或 `completion_tokens` 是整数时写 `model_usages`。成本只在调用方给出数字时记录。没有用量就保持空，不编造 token。Echo 和 ResearchStub 不写用量。

## Registry 与 Runner

`AgentRegistry.register(definition, factory)` 要求名称和显示名唯一，并要求版本、schema 和 enabled。`get` 创建实例。Registry 不管理 Docker、Execution 或 Conversation。

`AgentRunner.run` 是逻辑执行入口。Coding Agent 不在这里创建 Conversation。
