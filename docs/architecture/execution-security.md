# 执行安全边界

计划审批和工具审批是两道不同的门。

人工审批发生在 LangGraph 的 approval 节点。通过后才创建 worktree 和容器。拒绝则结束或重新规划。审批记录写在 ASE 业务库，工作流暂停点写在独立的 SQLite checkpoint。服务重启后按 workflow id 恢复，不会再次创建 worktree，也不会重放已经完成的分析节点。

容器内的工具不再使用 `NeverConfirm`。远端会话设置 `PatternSecurityAnalyzer` 和 `ConfirmRisky(threshold=MEDIUM, confirm_unknown=True)`。SDK 默认 HIGH 规则已经覆盖 `rm -rf`、`eval(`、`exec(`、`subprocess` 和下载后执行。ASE 额外把下面的内容标成 HIGH：

- `docker.sock`
- `--privileged`
- `pip install`
- `sudo`、`chmod`、`chown`

LOW 风险的文件读写和 pytest 可以在隔离容器里继续。MEDIUM、HIGH 和无法判断的操作会进入 `waiting_for_confirmation`。适配器调用 `reject_pending_actions`，不提供自动批准。因此高风险命令不会因为计划已经批准就放行。

允许的范围是授权 worktree 里的读写，以及该容器内的测试命令。禁止访问宿主机敏感目录、Docker socket、ASE 源码和生产密钥。Git 提交只发生在任务分支；`merge_to_main` 固定拒绝。容器执行失败不会改到宿主机 Python。

模型密钥可以进入 Agent Server 进程，因为远端推理循环在容器内运行。ASE 日志、事件和验收报告在落盘前替换密钥。不要把 `.env` 提交到 Git。
