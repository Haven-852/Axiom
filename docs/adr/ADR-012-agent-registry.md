# ADR-012 Agent Registry

状态：已接受。

运行时 Registry 是进程内静态目录。`register` 绑定 `AgentSpec` 和工厂。名称唯一，版本必填，缺省 Agent 抛出 `AgentNotFoundError`。

数据库里的 `agent_registry` 仍是启动时写入的规格目录，不负责创建实例，也不发现远程 Agent。

本阶段不建插件市场，也不做分布式服务发现。Echo 和 ResearchStub 用 `echo`、`research_stub` 注册，避免伪装成已经实现的 `research` 规格。
