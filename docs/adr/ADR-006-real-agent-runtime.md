# ADR-006 真实编码运行时

状态：已接受。

## 背景

Phase 1 用 `ScriptedCodingAdapter` 完成计算器流程，并用 TestLLM 验证 `Conversation.run()`。这两条路径都不是外部模型推理。Phase 1.1 需要用户需求经过分析、计划、人工审批、worktree、Agent Server 容器、OpenHands 和真实模型，再在容器内测试、诊断和修复。

## 决定

- 真实编码只走 `OpenHandsCodingAdapter`。`coding_runtime=openhands` 时启动官方 Agent Server 镜像，并用 `RemoteWorkspace` 建立 `RemoteConversation`。
- `ScriptedCodingAdapter` 保留给图和 API 的快速测试。TestLLM 保留给 SDK 回归。两者都不算真实执行。
- 模型配置集中在 `Settings`。DeepSeek 映射为 `openai/deepseek-flash`。诊断使用独立的 HTTP 客户端，失败时退回规则。
- pytest 只在已经启动的 Agent 容器里执行。容器缺失是错误，不是降级。
- 工具策略是 `ConfirmRisky`，高风险动作被拒绝。计划审批不能代替工具审批。
- 修复次数由 `ASE_MAX_REPAIR_ATTEMPTS` 限制。超过次数后进入评估，不继续循环。

## 后果

Agent Server 必须能访问模型 API，所以容器网络是 `bridge`，不是 `none`。模型密钥会随会话配置进入容器，但不会写入 ASE 日志。根文件系统不是只读。镜像缺失或 Docker 不可用时，工作流直接失败。

## 不采用

不修改 OpenHands 上游。不让 LangGraph 导入 SDK。不使用 `DockerWorkspace`，因为它的启动参数没有 ASE 要求的资源限制。不用宿主机 pytest 填补容器失败。
