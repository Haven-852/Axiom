# Phase 1.2 恢复测试

日期：2026-11-23。PostgreSQL 16 在 `127.0.0.1:5433`。OpenHands SDK `1.63.1`。模型仍是 `deepseek-flash`。

| 场景 | 注入方式 | 预期 | 实际 | 结果 |
| --- | --- | --- | --- | --- |
| PostgreSQL 重启 | `docker restart axiom-postgres-1` | 已提交的项目行仍在 | 重启后读到同一项目名 | 通过 |
| 审批期间杀死 ASE | `proc.kill()`，然后新进程批准 | 同一 workflow，不新增 Task，不新增 Approval，一个 worktree | `tests/test_process_recovery.py` 7.72 秒通过。编码运行时是 scripted，因为当时还没进 Agent | 通过 |
| 编码期间杀死 ASE | 真实 OpenHands 写出 conversation id 后 `proc.kill()`，容器保留，新进程调用 `/continue` | 不新建 Conversation | `tests/test_openhands_resume.py` 63.14 秒通过。容器在杀进程后仍在，恢复后的 conversation id 与杀死前相同 | 通过 |
| 测试已通过但评估未入库 | `ASE_CRASH_POINT=before_final_commit`，真实 DeepSeek | 同一 workflow、execution、attempt、conversation，补写评估和制品 | `tests/test_phase121_live.py` 第一轮通过。工作流 `7ad523ab-d605-4fae-b78d-5171684f6338`。编码 1 次，pytest 记录 2 次，评估和制品各 1 条 | 通过 |
| 数据库不可用 | 连接 `127.0.0.1:5999`，以及异常处理器 | 不返回成功，响应不含口令 | 启动直接失败。处理器返回 503，正文是 `database unavailable` | 通过 |
| 重复创建工作流 | 同一项目第二次 `POST /workflows` | 409 | `tests/test_persistence.py` 通过 | 通过 |
| 两个项目并行 | 两个线程，各自 GraphSandbox | worktree 和分支都不同，都 delivered | `tests/test_isolation.py` 7.14 秒通过。这是脚本化运行时 | 通过 |
| 两个容器 | 两个只读根容器 | 不同容器 id，文件不串，内存限制都在 | `tests/test_readonly_root.py` 通过 | 通过 |
| 只读根文件系统 | `ASE_AGENT_READONLY_ROOT=true`，镜像 `ase-agent-server:1.63.1` | inspect 为只读，worktree 可写，pytest 可用 | 先失败于 PyInstaller 无法映射 `libz.so.1`。`/tmp` tmpfs 改为 `exec` 后健康检查 200，测试通过 | 通过 |
| Agent Server 自身重启 | 真实 DeepSeek 运行中 `docker restart` 原容器 | 同一 conversation 继续，worktree 上的 `meta.json` 还在 | `test_agent_server_restart_keeps_the_conversation` 79.03 秒通过。工作流 `d9e4b1d9-00af-4405-8200-bb5f0acd93e6`，conversation `dd001a09-bb8f-415e-88c2-46adf8cf6e8c` | 通过 |
| 两个真实模型任务 | 一个 ASE 进程、两个仓库、两个线程 | 标识不串，两边都交付 | 两边都 `delivered`。一个任务有 develop 和 repair 两个 attempt。pytest 断言当时要求只有一个 conversation，所以命令失败；断言已改，没有重跑 | 通过，见 1.2.1 报告 |
| LLM 超时 | 本地套接字不响应 | 客户端超时且不回显口令 | `tests/test_faults.py::test_llm_request_times_out` 通过 | 通过 |
| pytest 超时 | 容器内命令超时 | 退出码 124 | `test_pytest_command_times_out_inside_container` 通过 | 通过 |
| PostgreSQL 暂停、事件回滚、重复提交、取消 | 暂停数据库，并在 scripted 工作流上取消 | 连接失败可恢复，事件回滚，409 | `test_postgres_pause_and_event_rollback` 通过 | 通过 |
| Agent Server 镜像缺失 | 不存在的镜像名 | 拒绝启动 | `test_missing_agent_image_is_refused` 通过 | 通过 |
| 模型 HTTP 500、取消在途 DeepSeek 请求 | 未注入 | 按失败或 interrupted 结束 | 没有对应实验 | 未验证 |

除 `tests/test_real_poc.py`、`tests/test_real_repair.py` 和已经单独通过的 `tests/test_openhands_resume.py` 之外，Phase 1.2 当时的回归是 26 项通过。1.2.1 的真实崩溃、重启和并发记录在 `docs/reports/phase-1.2.1-acceptance-report.md`。
