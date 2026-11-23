# 模块边界

| 模块 | 路径 | 可以依赖 |
| --- | --- | --- |
| 领域模型 | `src/ase/contracts/domain.py` | Pydantic |
| ORM | `src/ase/db/orm.py` | SQLAlchemy |
| 持久化 | `src/ase/store.py` | ORM 与领域模型 |
| 工作流 | `src/ase/workflow/` | 领域模型、store、adapter 抽象、Git、Sandbox |
| Coding 端口 | `src/ase/adapters/coding.py` | 领域模型 |
| OpenHands 适配器 | `src/ase/adapters/openhands.py` | `openhands.sdk` |
| API | `src/ase/api/router.py` | store 与 WorkflowService |

`src/ase/workflow/graph.py` 不导入 `openhands`。测试 `tests/test_openhands_adapter.py` 检查这份源码。

Pydantic 模型是模块之间的数据协议。ORM 行只在 `store.py` 里出现。LangGraph 的 `GraphState` 是可序列化字典，节点在边界上调用 `model_validate` / `model_dump`。

轻量 Agent 位于 `src/ase/agents/lightweight.py`。它们返回领域模型，不把自然语言直接写成业务记录。Coding Agent 的结果必须是 `AgentRunResult`。
