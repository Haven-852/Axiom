# 真实编码执行

这次 PoC 不用 `ScriptedCodingAdapter`，也不用 TestLLM。

需求原文：

> 为计算器增加支持科学计数法的表达式计算功能，要求支持合理的运算优先级、括号、浮点数，并补充完整的单元测试。

运行：

```powershell
$env:OPENHANDS_SUPPRESS_BANNER='1'
.\.venv\Scripts\python.exe -m pytest tests/test_real_poc.py -vv
```

结果：1 passed，63.45 秒。工作流状态 `delivered`。

实际链路：

1. ASE 把需求原文写成任务说明，并停在人工审批。
2. 批准后创建 git worktree，主分支指针不变。
3. 启动 `ghcr.io/openhands/agent-server:1.63.1-python`。inspect 确认内存 2147483648、1 CPU、pids 128、`CapDrop=ALL`、`no-new-privileges`、非特权、用户 `openhands`。
4. OpenHands `RemoteConversation` 使用 `openai/deepseek-flash`。
5. 模型用 `file_editor` 改写 `calculator.py` 和 `test_calculator.py`，用 `terminal` 在容器里运行 pytest，再调用 `finish`。
6. ASE 再次执行 `docker exec <container> python -m pytest -q`，退出码 0，输出 `65 passed in 0.09s`，耗时 0.598 秒。
7. 评估通过。任务分支提交 `11420b01a43f9294ff3aa5a876a9f84dbd316b36`。主分支没有被提交。

模型用量：prompt 63631，completion 4503，cache read 53760。日志中没有 API key。

产物：

- `docs/reports/real-poc.diff`
- `docs/reports/real-poc-log.json`

修复环单独验证：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_real_repair.py -vv
```

结果：1 passed，38.31 秒。第一次编码只允许 1 次迭代，容器内测试失败。诊断结果写明 `evaluate` 仍抛出 `NotImplementedError`。随后进入一次修复，`repair_count` 不超过 1。因为修复迭代同样被限制为 1，这次没有把功能做完；完整实现以 `tests/test_real_poc.py` 为准。
