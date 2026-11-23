# OpenHands 集成

适配器：`src/ase/adapters/openhands.py`。

已对照源码使用的调用：

- `LLM(model=..., api_key=..., base_url=...)`
- `Agent(llm=llm, tools=[])`。`FinishTool` 由 SDK 作为内建工具挂上，类名会变成工具名 `finish`。
- `Conversation(agent, workspace, max_iteration_per_run=...)`
- `conversation.send_message`、`run`、`interrupt`、`close`
- `conversation.state.execution_status`
- 远端会话使用 `PatternSecurityAnalyzer` 和 `ConfirmRisky`。TestLLM 本地会话不设置这套策略。
- 工具注册名是 `file_editor` 和 `terminal`。适配器先 `import openhands.tools`，再传 `Tool(name=...)`。
- `LocalWorkspace(working_dir=...)` 只在注入 TestLLM 且没有容器地址时使用。
- `RemoteWorkspace(host=..., working_dir="/workspace", api_key=会话令牌)` 用于真实编码。

`coding_runtime=openhands` 时由 `AgentContainerManager` 启动容器。容器缺失不会退回本机工作区。

`DockerWorkspace` 不在调用链上。原因写在 ADR-004。

脚本化适配器 `ScriptedCodingAdapter` 实现同一个 `CodingAgentAdapter`。它不是 OpenHands，验收报告里不会把它算作 SDK 集成。真实 SDK 测试在 `tests/test_openhands_sdk.py`，使用 SDK 自带的 `TestLLM`，不访问外部模型。
