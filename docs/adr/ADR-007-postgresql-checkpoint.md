# ADR-007 PostgreSQL checkpoint

状态：已接受。

LangGraph `1.2.24` 配合已安装的 `langgraph-checkpoint-postgres==3.2.1`。正式集成调用 `PostgresSaver.setup()`，表由这个包自己的迁移创建。

SQLite checkpointer 保留给本地和单元测试。`ASE_CHECKPOINT_BACKEND=auto` 按业务库地址选择。checkpoint 表和业务表同库不同表。State 不保存完整会话日志。
