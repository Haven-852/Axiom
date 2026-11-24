# Phase 2.5：Phase 2.4 真实 DeepSeek 失败诊断

诊断对象是一次性验收脚本留下的 SQLite 库 `var/phase24-real/ase.db`，以及该次进程的标准输出。这次运行没有写入 PostgreSQL。API Key 不出现在本报告中。

## 记录

| 项 | 值 |
| --- | --- |
| Execution | `349e8c88-c842-4496-a9fa-38e1abd712e4` |
| 状态 | `failed` |
| 创建 | `2026-11-23 09:16:13.566718` |
| 更新 | `2026-11-23 09:16:23.641724`（约 10 秒） |
| Attempt | 1，operation `failed` |
| Conversation | `a4b0f946-061d-48e0-b17b-f8f4bb711ea4` |
| Container | `null` |
| Execution.error | `null` |
| AgentRun | `8594cefe-e9de-4c22-979f-f0bb6a5bd647`，`failed` |
| Task | `637277f9-8d61-46a5-bd36-b480ebab9d78`，`failed` |
| execution_events | 0 行 |
| model_usages | 0 行 |
| 工作区 | `var/phase24-real/workspaces/job` |
| Git | 分支 `main`，`README.md` 仍为 `scope`，没有新 diff |

这次验收使用注入的 LLM，因此 Adapter 走了 `LocalWorkspace`，没有 Agent Server 容器。容器日志不存在。

## 1. 失败发生在哪个阶段

模型已经收到任务，并且成功返回了 Tool Call。Tool Call 在本机 PowerShell 中执行成功（exit code 0）。失败发生在文件修改之前：OpenHands 报 `Agent reached maximum iterations limit (6)`，Execution 被记为 `failed`。

## 2. 模型是否收到请求

收到了。进程日志中的用户消息就是 Coding Context：

- 任务：`Record the confirmed scope.`
- 类型：`documentation`
- 修改范围：`README.md`
- 验收：`The decision is recorded.`
- 用户决策：`无`

## 3. 模型是否返回

返回了。日志里有多轮 Agent Action。模型名为 `deepseek-flash`，提供方按 OpenAI 兼容接口调用。LiteLLM 成本表下载失败后使用了本地备份，并提示 `deepseek-flash` 未进入成本映射。这解释了日志里的 `$ 0.00`，不是编码失败原因。

## 4. 是否产生 Tool Call

产生了。可见工具是 `terminal` 和 `file_editor`。

## 5. Tool Call 是否执行成功

执行成功。`file_editor view` 读到 `README.md` 内容 `scope`。多次 `terminal` 的 exit code 都是 0。没有看到工具异常。

按日志顺序，模型做了这些事：

1. 列出工作区根目录。
2. 查看 `README.md`。
3. 查看 Git 历史和分支。
4. 递归列出工作区，并列出父目录。
5. 查看初始提交和 `.git/config`。
6. 列出 `var/phase24-real` 和 `var`。
7. 列出 `E:\demo\Axiom` 和 `E:\demo`。目录项里出现了 `.env` 的文件名。日志没有显示 `.env` 的内容。

没有任何 `file_editor` 的 `create` 或编辑命令。

## 6. 为什么没有文件修改

任务没有给出要写入的决策。验收标准只有 “The decision is recorded.”，用户决策是“无”。模型按 OpenHands 默认行为去探索，并且用绝对路径列出了工作区之外的目录。6 次迭代用完时，它还没有编辑 `README.md`。

## 7. 6 次迭代是否合理

这 6 次不是产品默认值。产品配置 `openhands_max_iterations` 的默认值是 40。这次一次性脚本把它设成了 6。日志中的限制来自 OpenHands 的 `max_iteration_per_run`。Adapter 外层还有一个最多 6 次 `conversation.run()` 的循环；这次是第一次 `run()` 就把内部 6 次迭代用完，所以外层循环不是这次的绑定限制。

不能把“把所有任务的上限调得很高”当作这次失败的修复。这 6 次被花在探索上，是因为任务本身没有可执行的修改内容。

## 8. 是否属于 Prompt 问题

是。证据：任务描述、验收标准和“用户决策：无”互相配不上，模型无法知道 README 应该写成什么。同时约束只写了“不访问仓库外文件”，模型仍然用绝对路径列出了父目录。LocalWorkspace 没有强制这个约束。

## 9. 是否属于模型配置问题

模型名和接口可用，模型返回了合法 Tool Call。成本映射缺失只影响费用估计。没有证据表明模型配置错误。

## 10. 是否属于 Runtime 问题

运行时没有崩溃，工具退出码为 0。选择 LocalWorkspace 使工具跑在宿主机上，所以模型可以列出工作区外目录。这是这次路径的隔离缺口，不是容器权限问题。没有容器退出码。

## 11. 是否属于 Tool Calling 问题

不是。工具调用格式被 OpenHands 接受并执行。

## 12. 是否属于上下文问题

进程日志里的累计用量大约是 input 43.83K、output 800。没有上下文超限或输出截断的记录。数据库没有 ModelUsage 行，因为当时的 Coding 路径没有把 OpenHands usage 写入 `model_usages`，也没有把事件写入 `execution_events`。停止原因只出现在进程日志和 AgentRun 的 summary 里，Execution.error 为空。

## 13. 应该修改什么

- Coding Context 要给出可执行的修改范围，并明确不要浏览工作区父目录。
- 真实编码验收必须使用有明确行为的独立仓库，不能复用这次含糊的 documentation fixture。
- 在执行开始前写入 Conversation ID，并在工具步骤后保存 Execution Event。能解析出的 token 计数写入 ModelUsage；解析不到时不编造。
- 失败时把停止原因写入 Execution.error。
- 真实 pytest 继续走现有容器沙箱。新的编码入口在远程运行时先准备或接管已有 Agent 容器，这样进程重启后还能找到同一个容器。
- 带 Plan Version 的 Workflow 不能再被旧生产图的 resume 直接拿去编码。

## 14. 不应该修改什么

- 不重写 OpenHands Adapter。
- 不提高全局迭代上限来掩盖含糊任务。默认 40 保持不变，并继续受超时限制。
- 不放宽 Docker 安全约束。
- 不把 TestLLM 或 Scripted Adapter 的成功当成这次真实模型成功。
- 不修改 calculator 的计划、澄清或批准状态。
