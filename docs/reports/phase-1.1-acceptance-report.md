# Phase 1.1 验收报告

日期：2026-11-22。Python 3.13.3，虚拟环境 `.venv`。OpenHands SDK 与 tools `1.63.1`。模型 `deepseek-flash`，接口 `https://api.deepseek.com`。密钥只存在于本机 `.env`，下文和 `docs/reports/real-poc-log.json` 都不包含它。

## 已完成，并在真实环境验证

| 验收项 | 证据 |
| --- | --- |
| 真实 LLM 调用 | `tests/test_llm_config.py` 返回 `ASE_OK`。编码会话累计 prompt 63631、completion 4503，模型名 `openai/deepseek-flash` |
| OpenHands 执行真实模型任务 | `tests/test_real_poc.py` 1 passed，63.45 秒。事件里有 `file_editor`、`terminal`、`finish` |
| 读取并修改项目文件 | `docs/reports/real-poc.diff` 改动 `calculator.py` 与 `test_calculator.py`。实现是递归下降解析器，数字正则覆盖科学计数法，没有 `eval` |
| 受控终端 | 同上会话中的 `terminal`。`tests/test_docker_agent.py` 里 `RemoteWorkspace.execute_command` 退出码 0 |
| Agent Server 容器启动 | 容器 `7aeefda7f7ec`，镜像 `ghcr.io/openhands/agent-server:1.63.1-python`。结束后 `docker ps -a --filter name=ase-agent` 为空 |
| inspect 资源限制 | memory `2147483648`，nano_cpus `1000000000`，pids `128`，privileged `false`，cap_drop `ALL`，security_opt `no-new-privileges`，绑定只有该 worktree 的 `:/workspace:rw`，没有 docker.sock |
| RemoteWorkspace | 连接 `127.0.0.1` 上的 Agent Server。本机 HTTP 代理会把回环地址转出并得到 502，ASE 在连接前把 `127.0.0.1` 和 `localhost` 写入 `NO_PROXY` |
| 容器内 pytest | 命令 `docker exec 7aeefda7f7ec... python -m pytest -q`，退出码 0，`65 passed in 0.09s`，记录耗时 0.598 秒。没有宿主机回退 |
| 失败进入诊断和一次修复 | `tests/test_real_repair.py` 1 passed，38.31 秒。诊断原文指出 `evaluate` 抛出 `NotImplementedError`。`Repair Completed` 只有一次 |
| 修复次数受限 | 该测试设置 `max_repair_attempts=1`，断言 `repair_count <= 1` |
| 人工审批后继续 | PoC 先停在 `waiting_approval`，批准后才出现容器和编码事件。审批 id `cc1d4ee5-fcc4-430c-b718-17050477e3c8` |
| 主分支未被修改 | 临时计算器仓库的 `main` 与批准前的 `HEAD` 相同。提交落在任务分支 |
| Git diff 与交付报告 | 评估摘要“测试通过且存在代码差异”。交付提交 `11420b01a43f9294ff3aa5a876a9f84dbd316b36`，artifact `6bc61595-32ac-4add-b0c6-e527b2aae8fa` |

## 已完成，但只通过 Mock、脚本或 TestLLM

| 项 | 验证方式 |
| --- | --- |
| SDK 工具循环回归 | `tests/test_openhands_sdk.py` 使用 TestLLM。它证明 `Conversation.run()`、`file_editor` 和 `finish`，不证明外部模型 |
| 图和 API 的快速闭环 | `ScriptedCodingAdapter` 加 `GraphSandbox`。它不启动容器，也不调用模型 |
| 审批拒绝、权限和 checkpoint 恢复 | `tests/test_workflow.py`、`tests/test_api.py`。新服务从 `checkpoints.sqlite` 恢复同一线程。这条没有在真实 OpenHands 进程被杀掉之后再跑一遍 |
| 修复上限的确定性分支 | `FlakyCodingAdapter` 第一次故意写坏实现，第二次写回可通过的代码。容器和模型不参与 |

## 部分完成

- PostgreSQL 16 已在 `127.0.0.1:5433` 上完成迁移和脚本化 API 闭环。真实编码 PoC 使用的是 SQLite 业务库。PostgreSQL 上还没有再跑一遍 OpenHands。
- 根文件系统不是只读。Agent Server 需要写运行目录，inspect 中 `readonly_rootfs` 为 false。
- `docker/agent-server.Dockerfile` 已提供，但没有构建。pytest 由 ASE 在官方镜像里按需安装。
- 容器内 `git status` 失败。Windows worktree 的 gitdir 指向宿主机路径，容器里解析成 `/workspace/C:/...`。ASE 的 diff 和提交在宿主机执行，所以交付仍然成立。代理被禁止自己提交。

## 未完成

- 没有在 PostgreSQL 上重放本次真实编码会话。
- 没有把真实 OpenHands 进程中断后，再用新进程恢复到审批点。恢复机制和脚本化测试是同一套 checkpoint，但没有用真实模型复测。
- 没有启用只读根文件系统。

## 测试结果

回归套件（不含两次真实编码）：19 passed，约 72 秒。其中包括真实 chat completion、TestLLM、Docker inspect、容器内 pytest、RemoteWorkspace、PostgreSQL 迁移和脚本化审批恢复。

另外两次真实编码：

- `tests/test_real_poc.py`：1 passed，63.45 秒。
- `tests/test_real_repair.py`：1 passed，38.31 秒。

合计 21 项通过。TestLLM 和 `ScriptedCodingAdapter` 的通过不能代替上面两行。
