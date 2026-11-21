# 仓库对照

分析日期：2026-11-21。结论只来自下列提交的源码，不来自 README 推断。本地浅克隆目录在 `E:\demo\axiom-src`，不进入本仓库。

| 项目 | 提交 | 许可证 | 主语言 | 在 ASE 中的位置 |
| --- | --- | --- | --- | --- |
| [OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) | `885242b563b1d2695d6c7b3d4bfe642f1c8838a3`（2026-11-21） | MIT | Python 3.12+ | **基础内核**。进程内依赖，不整仓 fork |
| [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) | `7975a21659ee700ec159279ca06a8ad29307a998`（2026-11-21） | MIT | TypeScript | Web / Electron 入口的参考。包名 `@openhands/agent-canvas` |
| [OpenHands/automation](https://github.com/OpenHands/automation) | `f2e72b5c8b615f9f739251bd2dcdc54fbc8152ec`（2026-11-20） | MIT | Python | PostgreSQL 任务模型的参考。它调用 agent-server，不内嵌 Agent |
| [FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT) | `11cdf466d042aece04fc6cfd13b28e1a70341b1f`（2026-01-21） | MIT | Python | 多角色消息环境的设计参考 |
| [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | `04d809ceab9df28f9adaed044884180159172930`（2026-09-03，包版本 2.4.6） | MIT | Python | 薄执行协议与评测循环的设计参考 |
| [Aider-AI/aider](https://github.com/Aider-AI/aider) | `5dc9490bb35f9729ef2c95d00a19ccd30c26339c`（2026-05-22，GitHub `main`） | Apache-2.0 | Python | Git 编辑、仓库地图、lint/test 闭环的设计参考 |
| [cline/cline](https://github.com/cline/cline) | `9f750b8a3dfbb441b629547d5d84aca130ea3828`（2026-11-21，GitHub `main`） | Apache-2.0 | TypeScript | IDE / CLI 双入口、工具审批回调的设计参考 |

七个仓库的依赖清单和已读源码里都没有 `langgraph`。ASE 的 LangGraph 编排需要自己写，不能从这些项目里搬出来。

## 和 ASE 技术栈的对齐

ASE 已确定：模块化单体、FastAPI、LangGraph、Pydantic V2、PostgreSQL、Docker、Git Worktree、Cursor 与 Web 双入口。执行层现在以 Python 为主，以后要能换 Go、C、C++ 执行器。

| 能力 | SDK | Agent Canvas | automation | MetaGPT | mini-swe-agent | aider | cline |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FastAPI 产品入口 | 有，`create_app` | 无，前端调 agent-server | 有，`_create_app` | 源码中未找到应用入口 | 源码中未找到 | 下载的 `aider/*.py` 中未找到 `FastAPI` | 无，gRPC / CLI / VS Code |
| Pydantic V2 | `pydantic>=2.14.3` | 不适用 | `pydantic>=2` | `pydantic>=2.5.3` | `pydantic >= 2.0` | `pydantic==2.12.5` | 不适用 |
| PostgreSQL | 依赖里有 SQLAlchemy，源码未 import | 未找到 | 有，SQLAlchemy 模型 + Alembic | 未找到 | 未找到 | 未找到 | SQLite 任务存储，不是 PostgreSQL |
| Docker 隔离执行 | `DockerWorkspace`，另有 agent-server docker runtime | UI 连接远程 runtime | 通过 sandbox 找 agent-server URL | 未作为 Agent 沙箱读到 | `DockerEnvironment.execute` 走 `docker exec` | `docker/Dockerfile` 是应用镜像，不是命令沙箱 | 未作为执行沙箱读到 |
| Git Worktree | agent-server `git worktree add` | UI 消费服务端会话 | 未在已读路径中找到 worktree 创建 | 未找到 | 未找到 | 未找到 | VS Code `createWorktree` |
| 人工审批 | `ConfirmationPolicy` + `WAITING_FOR_CONFIRMATION` | `useRespondToConfirmation` | 运行状态可取消，不是动作级确认 | `Role.is_human` 只是字段 | `InteractiveAgent` 的 `confirm` | 命令需用户输入，无策略对象 | `requestToolApproval` |
| 多 Agent | `register_agent`、`TaskTool`、子会话 | 展示子会话 | 一次运行对应一个 agent-server 会话 | `Team` + `Environment` | 单 Agent 协议 | `ArchitectCoder` 是编辑格式，不是运行时团队 | `AgentTeam`、`TeamChildSessionManager` |
| 可替换执行器 | `BaseWorkspace` / `RemoteWorkspace` | 不执行代码 | 不执行代码 | `Environment.execute` 不是语言无关 RPC | `Environment` Protocol | 直接 `subprocess` / `pexpect` | 工具在 TS runtime 内 |

## 结论

最适合作为 ASE 基础的是 **software-agent-sdk**，使用方式是依赖调用，不是把七个仓库合并进一个单体。

- Agent Canvas 负责现在的 Web 和 Electron，适合看交互，不适合作为 Python 单体的内核。
- automation 是唯一把 FastAPI、SQLAlchemy、Alembic、PostgreSQL 条件索引放在一起的服务，适合抄表结构和调度状态机。
- MetaGPT 的 `Environment.run` 用 `asyncio.gather` 并行跑角色，适合作为 LangGraph 里“角色节点”的设计对照，不适合当运行时。
- mini-swe-agent 的 `Environment.execute(action) -> dict` 是后续 Go/C/C++ 执行器最好抄的薄接口。
- aider 的价值在仓库地图、补丁应用和自动 lint，不在平台骨架。
- cline 的价值在 Cursor/VS Code 入口和审批回调，不在 Python 后端。

具体方案见 [technology-selection.md](technology-selection.md)。复用边界见 [code-reuse-mapping.md](code-reuse-mapping.md)。
