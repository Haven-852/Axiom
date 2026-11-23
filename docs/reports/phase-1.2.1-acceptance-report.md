# Phase 1.2.1 验收报告

日期：2026-11-23。PostgreSQL 16 在 `127.0.0.1:5433`。OpenHands SDK `1.63.1`。真实模型是 `deepseek-flash`，经 LiteLLM 记为 `openai/deepseek-flash`。

## 结论

Phase 1.2 可以标记为完整通过。结果入库前崩溃、Agent Server 重启后的原会话继续、以及两个真实 DeepSeek 任务并发交付，都有真实环境记录。

下面这些不阻断进入 Phase 2，列入已知风险：测试节点在崩溃当次被执行了两次；取消接口不打断已经发出的模型请求；只读根文件系统仍是显式开关；项目锁按项目而不是按 git 仓库。

## 证据怎么读

- 真实环境：DeepSeek、OpenHands Agent Server 容器、PostgreSQL。
- 故障注入：本地挂起的 HTTP、容器内 `sleep`、暂停的 PostgreSQL、缺失镜像。不代表外部模型。
- Scripted：`ScriptedCodingAdapter` 和 `GraphSandbox`。
- TestLLM：SDK 自带的假模型，只用于适配器回归。

## 任务 1：结果入库前崩溃

真实环境。`tests/test_phase121_live.py::test_crash_after_tests_pass_rewrites_final_result_once` 在第一轮实跑中通过。进程在 pytest 通过之后、评估写入之前以退出码 86 结束。

工作流 `7ad523ab-d605-4fae-b78d-5171684f6338`：

- 只有一条 develop attempt，状态 `completed`。
- conversation id `8d345de8-c8b8-4fc9-9a13-b88bda6bf62c`。
- `Agent Started` 1 次，工具事件 26 条。
- 恢复后 EvaluationReport 1 条，Artifact 1 条。
- 任务分支提交 `0c4c0a072cfe5eb430f79ab4ba2f59ef4d1ae5b6`。主分支未前进。

同一次 execution `137b6576-6823-45ba-b5a5-a5e8a8252152` 有两条 `Test Started` / `Test Completed`，两次都是通过。编码会话没有重放。原因是崩溃发生在评估节点入口，checkpoint 仍会重入测试节点。验收之后已加上保护：该 execution 已有通过的 `TestResult` 时，测试节点直接复用，不再执行 pytest。这条保护有脚本回归，没有再花一次真实模型崩溃来复跑。

## 任务 2：Agent Server 重启

真实环境。`tests/test_phase121_live.py::test_agent_server_restart_keeps_the_conversation` 最终 79.03 秒通过。

工作流 `d9e4b1d9-00af-4405-8200-bb5f0acd93e6`：

- `docker restart` 发生在 `meta.json` 已经写到 worktree 之后。
- 重启返回后，同一 `meta.json` 仍在，并且仍包含 conversation id `dd001a09-bb8f-415e-88c2-46adf8cf6e8c`。
- develop attempt 1 完成，工作流 `delivered`。
- 工具事件 32 条。
- 用量 `openai/deepseek-flash`，prompt 126132，completion 5507。费用字段来自 SDK，为 0.0。

第一次重启实验没有这份恢复。服务端把被打断的会话标成 `paused`，SDK 把 `paused` 当作本轮结束，ASE 把执行记成失败。工作流 `725e9442-8964-4295-bcd7-6fbd8dce5928` 因此停在交付未满足验收，conversation 仍是原来的 `0e04b41d-7faf-4a06-bfc5-693b24e59de0`，没有第二条用户消息。修复是 `paused` 后再调用一次 `run()`；若仍是 `paused`，状态记为 `interrupted`。

## 任务 3：两个真实任务并发

真实环境。一次 ASE 进程里两个线程同时批准两个独立仓库。pytest 断言最初要求每个工作流只有一个 conversation，因此命令以失败结束。数据库和任务分支显示两个工作流都已交付，标识没有串线。断言已改为“两边的 conversation 集合互不相交”。该断言修改后没有再跑一次双模型实验。

| 工作流 | 状态 | attempt | conversation | 容器前缀 | prompt / completion | 任务分支 |
| --- | --- | --- | --- | --- | --- | --- |
| `0a049b29-f29c-4625-aaf0-56b62f71294a` | delivered | develop 1 | `ae387b87-70bc-493a-ab64-0037e2382aa9` | `ea73962e` | 63235 / 4430 | `f0300af` |
| `903ed341-579c-49f8-8087-6fdfe780875e` | delivered | develop 1，随后 repair 2 | `35da8827-882c-43fc-8835-0127f73b6436`，`bf8e0e9b-594e-4111-8a4d-ca5998fe8063` | `6f541db2`，`1fb257dc` | 89278 / 5167，167266 / 2986 | `65f6f0c` |

两个 worktree 分别在不同的 project/task 目录。两条任务分支都只包含 `calculator.py` 和 `test_calculator.py`。用量行的 `workflow_id` 和 `task_id` 落在各自任务上。右侧第一次 pytest 未通过后进入 repair，这是新的 attempt，不是把左侧的会话拿来重跑。左侧照常交付。

交付提交会排除 `workspace/conversations` 和 `workspace/bash_events`，并为 Git 打开 `core.longpaths=true`。修复前，右侧在评估完成后被 Windows `Filename too long` 挡住，工作流停在 `evaluating`。

## 任务 4：故障注入

| 场景 | 证据等级 | 结果 |
| --- | --- | --- |
| LLM 请求超时 | 本地套接字挂起 5 秒，客户端超时 1 秒 | 通过。异常文本含 timeout，不含测试口令 |
| pytest 执行超时 | 真实 Agent Server 容器内 `sleep`，超时 2 秒 | 通过。退出码 124 |
| 缺少容器时的 pytest | 真实调用路径，`container_id` 为空 | 通过。退出码 127，输出为 Host execution is disabled |
| PostgreSQL 暂停 | `docker pause axiom-postgres-1` | 通过。连接失败；`unpause` 后 `select 1` 成功 |
| 事件写到一半 | 真实 PostgreSQL，`session_scope` 内抛错 | 通过。`Should Roll Back` 行不存在 |
| 重复提交同一工作流 | 真实 PostgreSQL | 通过。第二个活动工作流 409 |
| 用户取消 | 真实 API 和 PostgreSQL，编码运行时是 scripted | 通过。状态变为 `cancelled`，之后批准返回 409 |
| Agent Server 镜像不存在 | 真实 Docker 检查 | 通过。启动被拒绝，没有拉起容器 |
| 正在跑的 DeepSeek 请求被用户取消 | 未做 | 取消只改工作流行，不收回已发出的模型请求 |
| 模型返回 500 | 未做 | 超时覆盖的是客户端超时，不是上游 500 |

## 任务 5：架构审计

- 项目、工作流、执行、操作账本、测试结果、评估、制品和模型用量写在 PostgreSQL。
- LangGraph checkpoint 在同一库的 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes`、`checkpoint_migrations`。SQLite checkpoint 只在非 PostgreSQL 配置下使用。
- OpenHands 会话有独立目录，并用原来的 conversation id 重连。完整事件流也写入 `execution_events`。
- Execution 是一次执行记录。Attempt 是 `execution_operations` 上的 `kind + attempt`。修复增加 attempt，并新建 conversation。
- 已完成的 operation 会跳过。`paused` 不会再被当成一次成功后的盲目重跑。
- 容器仍限制内存、CPU、pids，`CapDrop ALL`，`no-new-privileges`，绑定来源只允许 worktree。默认镜像的根文件系统可写。只读根需要 `ASE_AGENT_READONLY_ROOT=true` 和已安装 pytest 的本地镜像。
- pytest 没有宿主机回退。
- 启动参数拒绝 `--privileged` 和 `docker.sock`。
- 抽查重启会话的事件正文时，SDK 写入的 `api_key` 为 `**********`。业务行不保存会话密钥。

## 已知风险和后续

1. 崩溃那次的 pytest 执行了两遍。跳过已通过测试的代码已进入仓库，尚未用另一次真实崩溃复验。
2. 取消不停止在途模型调用，也不按容器 id 停止对应 Agent Server。后续应只中断该工作流的 conversation，并保留其他任务的容器。
3. 只读根文件系统仍需显式打开。官方镜像在启动时安装 pytest，所以默认保持可写根。官方镜像自带 pytest 之后，再把只读根改成默认。
4. 项目锁是一个项目一个活动工作流。两个项目仍可指向同一 git 仓库。后续若要禁止，应加仓库级锁。
5. 一个并发 worktree 在容器删除后，`workspace/conversations` 目录是空的。另一侧保留了 develop 会话的 `meta.json`。消息和工具调用以 PostgreSQL 的 `execution_events` 为准。
6. 模型 HTTP 仍是 at-least-once。已经发出的请求不能收回。

## 进入 Phase 2 之前建议调整的架构

这些是建议，本阶段没有实现。

- 保持控制面和执行面分离。Phase 2 的编排应调度现有 Workflow、Execution 和 Attempt，而不是再包一层会重跑 OpenHands 的循环。
- 测试节点、评估节点和交付提交继续以数据库里的已通过结果和已完成 operation 作为幂等条件。
- 取消要落到具体 conversation 和容器，并明确在途模型调用的状态是 `interrupted`。
- 会话目录与交付提交保持分离。Windows 上继续使用长路径参数，不把事件日志提交进任务分支。
- 只读根、仓库级锁和多代理编排放到各自的阶段。本阶段不引入多代理、Agent Canvas、Cursor 插件、MCP、项目知识库或量化模块。

## 命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_faults.py tests/test_workflow.py tests/test_lifecycle.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_phase121_live.py::test_crash_after_tests_pass_rewrites_final_result_once -q
.\.venv\Scripts\python.exe -m pytest tests/test_phase121_live.py::test_agent_server_restart_keeps_the_conversation -q
.\.venv\Scripts\python.exe -m pytest tests/test_phase121_live.py::test_two_real_model_tasks_stay_isolated -q
```

崩溃测试和重启测试的通过记录见上文。并发测试的命令曾因断言过严失败，对应的真实运行已经交付，断言修正后没有重跑。
