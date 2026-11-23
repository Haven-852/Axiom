# Phase 2.1 初始审计

日期：2026-11-23。审计在实现 Research Agent 之前完成。已发布提交仍是 `710105c` 和 `8dcac16`。Phase 1 到 2.0 的代码在工作区，尚未提交。

## 1. DeepSeek 是否已有统一 LLM Adapter

有。`src/ase/agents/llm_client.py` 的 `complete()` 用现有 Settings 调用 OpenAI 兼容的 `/chat/completions`。诊断器 `diagnose_failure()` 已经走这个函数。该模块禁止导入 OpenHands。

OpenHands 编码路径自己配置模型，不经过 `complete()`。Research Agent 应调用 `complete()`，不能接入 OpenHands。

`complete()` 目前没有 JSON mode 参数。用量在返回的 log 里，只有响应体带出的数字才算数。

## 2. Agent Runner 如何获得 Agent 实例

`AgentRunner.run` 用 `AgentRegistry.get(name)` 调用工厂。工厂在注册时绑定。Runner 不负责 Docker 或 Conversation。

相同 `idempotency_key` 会直接返回已有 AgentRun，包括未完成和失败记录。Phase 2.1 需要收紧：只有 `completed` 可以复用；`pending`、`running`、`interrupted` 允许在同一行上继续；失败和超时不能被当成成功。

## 3. AgentContext 能否读项目文件

不能。Context 只有 workflow、project、task、correlation、artifact 和 execution 标识，以及取消检查。没有仓库路径，也没有文件读取接口。

项目路径在 `projects.repo_path`。Research Agent 应通过 `project_id` 读取这一行，再交给只读 ProjectReader。不能把整个仓库放进 Context。

## 4. Artifact 如何保存并关联 AgentRun

现有 `artifacts` 表有 `project_id`、`workflow_id`、`execution_id`、`kind`、`uri`、`body`。没有单独的 `agent_run_id` 列，也不另建表。

Runner 从 `AgentResult.metadata["artifacts"]` 写 Artifact，并把 id 写入 `agent_runs.output_artifact_ids`。Research 报告沿用这个机制：`kind=research_report`，正文 JSON 自带 `schema_version` 和 `agent_run_id`。相同 uri 应复用已有行，避免恢复时重复插入。

## 5. ModelUsage 如何关联 agent_run_id

`model_usages.agent_name` 和 `agent_run_id` 已经存在。Runner 只在 `prompt_tokens` 或 `completion_tokens` 是整数时写入。`estimated_cost` 不是数字就保持空。Research Agent 把 `complete()` 返回的真实 usage 放进 metadata，不能编造。

## 6. 输入输出快照

Runner 在执行前把脱敏后的输入写入 `input_snapshot`，结束时把输出写入 `output_snapshot`。状态、开始时间和结束时间写在同一行。异常对象不入库。

## 7. LangGraph 如何启动和恢复

生产图由 `WorkflowService.start` 启动，并获取 Repository Lock。Checkpoint 由 `build_context` 按 `checkpoint_backend` 选择 PostgresSaver 或 SqliteSaver。

`src/ase/orchestration/` 是另一张图：ResearchStub 然后 Echo。节点通过 Agent Runner。SqliteSaver 不支持异步，节点保持同步。Stub 和 Echo 继续留作回归，不删除。

Research 工作流应新建图，不替换生产编码图，也不获取仓库写锁。

## 8. ResearchStub 能否直接替换

不能。Stub 固定返回 `summary` 和 `findings`，不读仓库，不调用模型。真实 Research Agent 使用新的 `research` 注册名和新的报告 schema。Stub 图保持原样。

领域模型里已有 `RequirementAnalysis`，它是 Phase 1 计划器用的短记录。研究报告里的同名结构放在 Research 包内，不覆盖旧类型。

## 9. 现有文件读取和搜索

没有面向 Project 的只读文件接口。`GitWorkspaceManager` 会创建 worktree 和分支，属于写路径。Research 不能使用它来读代码。

需要新的 ProjectReader：相对路径、拒绝 `..` 和越界符号链接、限制大小和数量、跳过 `.git`、依赖目录和密钥文件。Git 只执行 `rev-parse`、`status` 和 `diff --stat`。

## 10. 直接复用的模块

- `llm_client.complete`
- `AgentRunner`、`AgentRegistry`、`AgentContext`、`AgentResult`、`AgentRun`
- `save_artifact`、`save_model_usage`、`save_agent_run`
- Settings 里的 provider、model、base、key、timeout、temperature、max_tokens
- 现有 checkpoint 实现
- `WorkflowConflict` 和项目级 active workflow 检查

不修改 `src/ase/adapters/openhands.py`，不改 `_run_coding`。
