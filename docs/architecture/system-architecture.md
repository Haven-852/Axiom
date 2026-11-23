# ASE Phase 1 系统架构

ASE 是控制面。OpenHands SDK 是编码执行面。LangGraph 只编排一次研发流程，PostgreSQL 保存项目、需求、任务、审批、执行和产物。

```text
FastAPI
  -> WorkflowService
       -> LangGraph StateGraph
            -> 轻量 Research / Planning / Diagnosis / Evaluation
            -> CodingAgentAdapter
                 -> ScriptedCodingAdapter（无模型时的可重复执行器）
                 -> OpenHandsCodingAdapter（唯一允许 import openhands.sdk 的模块）
            -> GitWorkspaceManager
            -> DockerSandbox
  -> SQLAlchemy / PostgreSQL 或 sqlite
```

已核对的 SDK 版本是 `software-agent-sdk` `885242b`，包版本 `1.63.1`。公开入口是 `Agent`、`LLM`、`Conversation`、`LocalWorkspace`、`RemoteWorkspace`。`Conversation(...)` 在本地工作区上创建 `LocalConversation`，在 `RemoteWorkspace` 上创建 `RemoteConversation`。

`DockerWorkspace._start_container` 的 `docker run` 不包含 `--memory`、`--cpus`、`--cap-drop` 或 `no-new-privileges`。Phase 1 因此不调用 `DockerWorkspace`。ASE 自己生成带这些限制的容器参数，再通过 `RemoteWorkspace(host=..., working_dir="/workspace")` 接入已经启动的 agent server。没有 `ASE_OPENHANDS_HOST` 时，适配器使用 `LocalWorkspace`，并且工作区路径必须落在 `ASE_WORKSPACE_ROOT` 内。

这和 `docs/adr/0001-ase-v1-control-plane.md` 的差异见 `docs/adr/ADR-004-workspace-isolation.md`。0001 继续保留，Phase 1 的 ADR 说明哪些条款被本阶段规格改写。
