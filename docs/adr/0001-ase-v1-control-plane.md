# ADR 0001：ASE V1 只做控制面，执行面调用 OpenHands

- 状态：**提议，等待人工批准**
- 日期：2026-11-22
- 决定人：待批准
- 取代：`analysis/technology-selection.md` 里「进程内 `LocalConversation`、自建 Web、ASE 自己执行 `git worktree add`」的实现建议。分析结论本身仍然有效。
- 规格：`docs/design/ase-v1.md`

## 背景

源码分析已经确定软件研发内核来自 OpenHands software-agent-sdk `885242b`（包版本 1.63.1），Web 来自 OpenHands Agent Canvas `7975a21`（`@openhands/agent-canvas` 1.43.0）。七个候选仓库都没有 LangGraph。Agent Server 已经提供 Workspace、终端、文件工具、Git worktree 和 Docker runtime。

后续约束是：尽量复用 SDK；优先复用 Canvas 的 Web 组件；不重写 Workspace、Tools、Terminal 和 Agent Server；LangGraph 负责 ASE 主工作流；Pydantic V2 负责模块间协议；保持模块化单体；第一个验收项目是量化研究系统。

## 决定

1. ASE 是一个 Python 模块化单体，只拥有研究工作流、PostgreSQL 索引、阶段审批和量化领域 API。
2. 不复制 OpenHands 或 Agent Canvas 的仓库。执行面以独立进程运行已发布的 `openhands-agent-server`，版本钉死。
3. ASE 里只有 `adapters` 包可以导入 `openhands.sdk` 的请求模型。其余模块依赖 ASE 自己的 Pydantic 模型。
4. Web 壳依赖 npm 包 `@openhands/agent-canvas` 的 `conversation`、`files`、`terminal` 导出，以及 `AgentServerUIRoot`。不 fork 前端仓库。
5. Git worktree 与 Docker 沙箱只通过 `StartConversationRequest.worktree`、`worktree_options`、`conversation_runtime`、`container` 交给 Agent Server。ASE 不实现 `git worktree add`，也不实现第二套终端。
6. 工具级审批沿用 Agent Server 的 `ConfirmRisky` 和 `POST /respond_to_confirmation`。阶段级审批由 LangGraph `interrupt` 写入 PostgreSQL。
7. 在本 ADR 被批准之前，不创建 `src/ase/`、`web/` 或 Alembic 迁移。

## 后果

- 会话事件仍以 Agent Server 的 `base_state.json` 为事实来源。PostgreSQL 只存 run、绑定和审批。
- 升级 SDK 或 Canvas 必须单独开 ADR，因为 UI 组件和 HTTP 模型是一对版本。
- 量化研究 V1 只做到假设、实现、容器内回测和人工接受。不包含实盘下单。
- Cursor 第一期调用同一套 ASE HTTP API，不另做编辑器插件。

## 批准

批准本文件即批准 `docs/design/ase-v1.md` 的复用表、模块、适配器、工作流、数据模型、API、worktree、沙箱、审批、许可证和待建文件清单。拒绝或改范围时，只改这两份文档，不开始写应用代码。
