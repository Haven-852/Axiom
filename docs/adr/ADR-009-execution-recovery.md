# ADR-009 执行恢复

状态：已接受。

外部操作使用稳定的 operation id。完成的操作直接跳过。未完成但已经有 conversation id 的操作重新连接原会话。修复才使用新的 attempt。

同一项目的第二个活动工作流被拒绝。服务进程被强制终止后，容器不一定被清理；新进程按 Docker 标签采纳它。

这不是分布式 exactly-once。已经发出的模型请求不能取消，恢复语义是 at-least-once 加幂等键。
