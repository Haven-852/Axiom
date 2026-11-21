# AXIOM

Autonomous eXecution & Intelligent Orchestration Machine。

本仓库当前只包含对七个开源项目的源码级架构分析，供 ASE（Autonomous Software Engineering Platform）二次开发选型。没有改动任何上游代码，也没有把上游仓库嵌进来。

目标技术栈：模块化单体、FastAPI、LangGraph、Pydantic V2、PostgreSQL、Docker、Git Worktree、Cursor 与 Web 双入口。执行层以 Python 为主，并为后续 Go、C、C++ 执行器留接口。

## 分析文档

| 文档 | 内容 |
| --- | --- |
| [analysis/repository-comparison.md](analysis/repository-comparison.md) | 七个仓库对照与结论 |
| [analysis/architecture-analysis.md](analysis/architecture-analysis.md) | 工程结构、入口、配置、模块依赖 |
| [analysis/agent-design-analysis.md](analysis/agent-design-analysis.md) | Agent 创建、注册、执行、上下文、工具、异常 |
| [analysis/workflow-analysis.md](analysis/workflow-analysis.md) | 任务拆解、协作、并行、中断恢复、人工审批 |
| [analysis/execution-engine-analysis.md](analysis/execution-engine-analysis.md) | 文件、Shell、Git、Workspace、Docker、诊断 |
| [analysis/security-analysis.md](analysis/security-analysis.md) | 权限、确认、隔离、密钥 |
| [analysis/code-reuse-mapping.md](analysis/code-reuse-mapping.md) | 可复用、API 调用、只参考、不引入 |
| [analysis/technology-selection.md](analysis/technology-selection.md) | ASE 基础项目与二次开发方案 |

结论：以 [OpenHands/software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) 为 Agent 与执行内核，ASE 自己实现 LangGraph 编排和 PostgreSQL 状态；其余项目只借鉴设计。
