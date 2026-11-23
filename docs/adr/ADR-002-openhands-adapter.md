# ADR-002 OpenHands 适配器

状态：已接受。

ASE 只在 `src/ase/adapters/openhands.py` 导入 `openhands.sdk`。工作流节点依赖 `CodingAgentAdapter`。

适配器调用 SDK 1.63.1 的真实类型：`LLM`、`Agent`、`Conversation`、`LocalWorkspace`、`RemoteWorkspace`。会话生命周期使用 `send_message`、`run`、`interrupt`、`close` 和 `execution_status`。

同一个端口上还有 `ScriptedCodingAdapter`。它用于没有模型密钥时把计算器 PoC 跑完。它不代替 SDK 集成测试。

后续如果接入 Aider 或 mini-swe-agent，新增适配器，不改工作流节点。
