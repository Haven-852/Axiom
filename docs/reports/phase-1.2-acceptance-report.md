# Phase 1.2 验收报告

日期：2026-11-23。

## 已完成，并在真实环境验证

- PostgreSQL 上的 Alembic 升级到 `0002_phase12`。业务表新增操作账本和模型用量表。`PostgresSaver.setup()` 创建了官方 checkpoint 表。
- 同一项目的第二个活动工作流返回 409。PostgreSQL 重启后，已提交的项目行还在。
- ASE 进程在审批处被杀死后，新进程沿同一 workflow id 批准。Task 数和 Approval 数不变，worktree 只有一个。这次编码运行时是 scripted。
- 真实 OpenHands 运行在写出 conversation id 后被杀死。Agent 容器仍在。新进程继续同一 conversation id，没有第二个 id。
- 只读根文件系统在镜像 `ase-agent-server:1.63.1` 上通过 inspect。用户是 `openhands`。`/tmp` 必须是可执行 tmpfs，否则 PyInstaller 无法加载 `libz.so.1`。worktree 可写，容器内 pytest 可用。
- 两个只读容器同时运行时，文件、容器 id 和内存限制彼此独立。

## 已完成，但只通过脚本或 TestLLM

- 两个项目并行交付使用 `ScriptedCodingAdapter` 和 `GraphSandbox`。
- 审批恢复的编码阶段也是 scripted。checkpoint 和进程杀死是真实的。
- TestLLM 回归仍在，不代表外部模型。

## 部分完成

- 模型用量表和查询接口已经接上。恢复测试证明了 conversation id，没有再单独查询一条 token 记录。
- 只读根文件系统需要显式打开，并使用预装 pytest 的自建镜像。默认路径仍是官方镜像和可写根文件系统。
- 容器意外消失时，pytest 会为同一 worktree 再启动容器。没有做一次中途 `docker kill` 的实验。
- 项目锁按项目，不按 git 仓库。两个项目仍可指向同一仓库。

## 未完成

Phase 1.2 初版没有单独跑完下面几项。Phase 1.2.1 的真实结果写在 `docs/reports/phase-1.2.1-acceptance-report.md`。

- 结果入库前崩溃、Agent Server 重启和两个 DeepSeek 任务并发，已在 1.2.1 用真实模型跑过。
- 模型 HTTP 500 和“取消正在进行的 DeepSeek 请求”仍未做。
- `before_execution_commit` 这个更早的钩子仍没有单独再跑。1.2.1 跑的是测试已经通过之后的 `before_final_commit`。

## 命令

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m pytest tests/test_persistence.py tests/test_process_recovery.py tests/test_openhands_resume.py tests/test_readonly_root.py tests/test_isolation.py -q
```

`tests/test_openhands_resume.py` 会调用真实模型。其余新增测试不调用模型。

## Phase 1.2.1 之后的总评

关键故障恢复、幂等补写和真实双任务并发已经有真实环境记录。Phase 1.2 标记为完整通过。残留项和 Phase 2 之前的调整见 `docs/reports/phase-1.2.1-acceptance-report.md`。
