# Phase 2.5：Conversation 中断恢复

恢复语义仍是 at-least-once。模型请求不能撤回。进程死在请求发出之后、结果写完之前时，恢复可能再发一次请求。

## 场景 A：Conversation 已写入，任务未完成

测试任务 `3354a35d-b640-5afe-b45e-e1dcc0101bba`。

| 时点 | Execution | Conversation | 状态 | Events |
| --- | --- | --- | --- | --- |
| 杀死前 | `380cc9dd-7577-44b8-9940-9d2eb09e03a0` | `b60722f7-035b-43e5-861e-711a8487d61f` | `running` | 2 |
| 杀死后、恢复前 | 同一个 | 同一个 | `running` | 至少 2 |
| 恢复后 | 同一个 | 同一个 | `completed` | 31 |

Attempt 仍是 1。这个 Workflow 的 Execution 总数是 2：实现任务一条，测试任务一条。没有第二条测试任务 Execution，也没有第二个 Conversation。Operation 也是两条，和两个任务对应。已有事件没有被删掉，恢复后数量从 2 增加到 31。

杀死方式是 `taskkill /F /T`。Agent 容器和 worktree 没有删除。新进程通过容器标签接管了原来的容器，并把原来的 Conversation ID 传回 OpenHands。

Coding AgentRun `26fe26ec-a968-47d8-af7e-9322a34441ce`。OpenHands usage 行是 prompt 36618，completion 1215，total 37833。模型列同样是 `unknown`，原因和实现任务相同。

## 场景 B：文件已经修改，pytest 还没跑

实现任务的文件修改发生在 Execution `3838f44c-b623-4250-aa33-6c942044abd9` 内。`conversation.run()` 在一次调用里完成了编辑。监视进程看到事件时，这条 Execution 已经提交为 `completed`。随后的第二次调用返回 `completed operation skipped`。

所以这次没有把“文件已改、Execution 仍在 running、pytest 未开始”的崩溃注入进去。替代证据是：文件修改留在任务分支上，pytest 由另一次进程调用现有 `DockerSandbox.run_pytest` 执行，并且没有再创建实现任务的 Execution。

## 场景 C：pytest 已完成，评估还没写

TestResult `0bb39e7e-020d-465f-b9ed-a93912af9303` 先写入。然后启动一个会先等待再评估的进程，并在等待期间杀死它。之后的新进程写入唯一的 Evaluation `1c3f1b24-2b5d-4486-8ef5-57239a269d75`。TestResult 没有被覆盖，数量仍是 1。

这个时间点是验收脚本里的进程边界。Plan 管理的编码入口本身不在同一次调用里串起 pytest 和 Evaluation，所以生产代码里没有一个可以在这两步之间可靠杀死的单进程节点。

## 状态

实现任务和测试任务最终都是 `completed`。两条 Execution 都是 `completed`。对应 AgentRun 都是 `completed`。Workflow 是 `delivered`。失败的 Execution 没有被改成成功。
