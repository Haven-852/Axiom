# ADR-001 模块化单体

状态：已接受，用于 Phase 1。

`docs/adr/0001-ase-v1-control-plane.md` 仍是控制面与执行面分离的决定。本 ADR 补充 Phase 1 的落地边界。

ASE 是一个进程内的模块化单体。FastAPI、LangGraph、领域服务和适配器放在 `src/ase`。OpenHands 不拥有需求分析、计划、审批、测试路由或交付。编码执行通过 `CodingAgentAdapter` 出去。

Phase 1 不修改 Agent Canvas，不实现 Cursor 插件，也不把量化研究作为验收项目。验收项目是 `ase-poc-demo/calculator`。

业务状态在 SQL。LangGraph checkpointer 只负责中断后的恢复。
