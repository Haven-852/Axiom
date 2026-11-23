# Phase 1 验收报告

日期：2026-11-22。Python 3.13 虚拟环境 `.venv`。OpenHands SDK / tools `1.63.1`。

## 代码库检查

`E:\demo\Axiom` 在本阶段开始时只有 `analysis/`、`README.md`、`docs/adr/0001-ase-v1-control-plane.md` 和 `docs/design/ase-v1.md`。没有应用代码。

SDK 源码在 `E:\demo\axiom-src\software-agent-sdk`，提交 `885242b`。本阶段没有修改上游。调用以安装后的 `openhands-sdk==1.63.1` 为准。

已核对的事实：

- `Conversation.state.execution_status` 才是状态字段。`LocalConversation` 本身没有 `execution_status`。
- 工具注册名是 `file_editor`。`Tool(name="FileEditorTool")` 会抛出未注册错误。
- `DockerWorkspace` 的启动命令没有内存、CPU 和 capability 限制。ASE 不调用它。

## 已跑通

`pytest`：13 passed。

闭环用 `ScriptedCodingAdapter` 在独立 git worktree 的 `ase/<task>` 分支上改写 `calculator.py` 和 `test_calculator.py`，宿主机 pytest 通过，状态 `delivered`。审批中断后用同一个 workflow id 恢复。新的服务实例从 `checkpoints.sqlite` 恢复同一条线程。拒绝审批会停在 `cancelled`，不创建 worktree。测试失败会进入诊断和修复，次数受 `max_repairs` 限制。

产物：

- `docs/reports/sample-git.diff`
- `docs/reports/execution-log.json`

OpenHands 集成测试 `tests/test_openhands_sdk.py` 使用 SDK 自带的 `TestLLM`，真实执行 `Conversation.run()`。脚本化的模型先调用 `file_editor` 写入 `sdk_edit.txt`，再调用 `finish`。测试断言文件内容。失败路径由适配器捕获成 `AgentRunResult.error`，工作流节点不导入 `openhands`。

`merge_to_main` 会拒绝。工作区路径不能落在 ASE 的 `src`、`docs`、`analysis`、`tests`。Docker 参数包含内存、CPU、pid、`--cap-drop=ALL`、`no-new-privileges`，并且拒绝 `--privileged` 和 `docker.sock`。

## 未完成或有偏差

1. 计算器 PoC 的代码改动来自脚本化适配器，不是一次真实模型推理。OpenHands 测试证明了 SDK 会话和 `file_editor`，没有把科学计数法实现交给模型生成。
2. 本机没有 `python:3.12-slim`，也没有 `ghcr.io/openhands/agent-server`。pytest 在 `ASE_ALLOW_HOST_TESTS=1` 时退回宿主机。agent server 容器只生成了命令，没有启动。`RemoteWorkspace` 分支还没有对着活的服务跑过。
3. 业务测试使用 sqlite。`docker-compose.yml` 里的 PostgreSQL 16 没有在本次验收中启动。
4. Research、Planning、Diagnosis、Evaluation 是确定性函数，返回 Pydantic 模型。它们没有调用语言模型。
5. 编码会话在计划审批之后使用 `NeverConfirm`，并且没有注册 `terminal`。工具级风险确认还没有接到 API。
6. 没有前端，没有 Agent Canvas 改动。

这些偏差不改变 Phase 1 的模块边界。下一阶段应补上：启动加固后的 agent server、用 `RemoteWorkspace` 跑一次真实模型编辑、把 pytest 放进已有镜像的容器，并把 PostgreSQL 作为默认业务库跑一遍迁移。
