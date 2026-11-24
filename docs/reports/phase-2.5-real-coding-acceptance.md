# Phase 2.5：真实编码验收

这次验收使用真实 DeepSeek。它不是 TestLLM，也不是 Scripted Adapter。

独立仓库：`E:\demo\ase-phase25-coding-validation`。它不在 Axiom 仓库内，也不使用 calculator 的项目、Workflow 或批准记录。

## 级别

| 检查 | 结果 |
| --- | --- |
| 非真实模型回归 | 在加入对话回调之前，完整非真实模型套件退出码 0。回调改动后的复跑见提交说明。 |
| OpenHands SDK TestLLM | Phase 2.4 已通过，本阶段不把它当作真实模型成功。 |
| 真实 DeepSeek | 成功。模型 `deepseek-flash`。 |
| 真实 pytest | 成功。容器内 `3 passed`。 |
| Conversation 恢复 | 测试任务的 Execution 在 `running` 时被杀死，随后同一 Execution 和同一 Conversation 完成。实现任务的那次杀死发生在 Execution 已经完成之后，不能算作进行中的恢复。 |

## 计划与批准

| 项 | 值 |
| --- | --- |
| Project | `2e3a4447-7b17-4a74-bc0b-4a689281c56a` |
| Workflow | `ec64e22e-88d7-4e65-9871-112cee68bb77` |
| Research AgentRun | `8be8e792-0c8a-478c-8b83-217f082f8de8` |
| Research Artifact | `c94e0a1c-cea1-4a27-8052-5c1bd94458c6` |
| Research usage | prompt 2128，completion 3329，total 5457，latency 23.698 秒，cost null，model `deepseek-flash` |
| Planning AgentRun | `e7a5039c-da84-456c-9174-540a8bce0093` |
| Plan | `e478354e-98f8-56b9-a8be-d001c8d9e9e2` |
| Plan Version | 1 |
| Plan Artifact | `a1088da9-2471-4a3e-a479-151b1682c509` |
| Plan Hash | `e9e83248631df9807ae515a4010e2f29a3a9d5116378afe005d1c42682fba432` |
| Planning usage | prompt 3401，completion 1676，total 5077，latency 10.558 秒，cost null，model `deepseek-flash` |
| Approval | `2e25b06d-cbe2-4ed4-8883-af858a9f8c5c`，status `approved`，actor `api-key`，时间 `2026-11-24T01:10:27.631148+00:00` |
| 澄清 | 没有阻塞澄清，没有调用回答接口 |

实现任务 `d4f256a8-8468-54d8-bf7b-71cb6d4c97ce`，标题“实现 normalize_text 函数体”，文件 `src/string_utils.py`。测试任务 `3354a35d-b640-5afe-b45e-e1dcc0101bba` 依赖它。批准走 `PlanReviewService.decide`，没有直接改数据库。

## 编码

| 项 | 值 |
| --- | --- |
| Coding AgentRun | `f8fe4c24-981c-46e5-bb5e-02214e27a82f`，status `completed` |
| Execution | `3838f44c-b623-4250-aa33-6c942044abd9`，status `completed`，attempt 1 |
| Conversation | `6fa831ea-0cb4-4b6c-9a50-241601e8acb8` |
| Container | `edf52f8127aa75c8a7aa01bad0bf9a36a0df51166fa71dc195c6fc323f9a7fe6` |
| OpenHands usage 行 | prompt 27350，completion 648，total 27998。模型列是 `unknown`，因为 SDK 统计里没有模型名。进程日志中的请求是 `deepseek-flash`。cost null。 |
| 工具 | `file_editor` 对 `/workspace/src/string_utils.py` 执行 `str_replace`，结果是 `edited with 1 changes`。 |
| 分支 | `ase/d4f256a8-8468-54d8-bf7b-71cb6d4c97ce` |
| 提交 | `a3314fce617c4d68db2f149ae7493a30625da515` |
| 主分支 | `4d5448c9ed58c1907498d8d8ee04c326ba110269`，仍是初始提交 |

函数现在是：

```python
import re

_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    return _WHITESPACE_RUN.sub(" ", value).strip().lower()
```

`tests/test_string_utils.py` 没有被修改。交付提交还包含 pytest 产生的 `__pycache__`。这是 `git add -A` 的结果，不是测试被放宽。

## pytest、评估、交付

TestResult `0bb39e7e-020d-465f-b9ed-a93912af9303`，passed true，exit code 0。命令是容器内的 `python -m pytest -q`。输出末尾是 `3 passed in 0.16s`。这个 Workflow 只有这一条 TestResult。

Evaluation `1c3f1b24-2b5d-4486-8ef5-57239a269d75`，passed true，summary“测试通过且存在代码差异”。

Workflow 状态 `delivered`。Artifact `git_diff` 的 uri 是 `workspace://202b456d-6a71-460e-b3c1-6a6fc6e3d7be`。实现任务状态 `completed`。测试任务在恢复实验后也是 `completed`。

## 容器

验收后仍在运行的 Agent 容器是 `openhands` 用户，`Privileged=false`，`CapDrop=["ALL"]`，内存 2147483648，NanoCpus 1000000000，PidsLimit 128，`no-new-privileges`。挂载只有该 worktree 到 `/workspace`，没有 `docker.sock`。

## calculator

Workflow `1dd7c84d-36d1-4dcb-a66e-1004e0a1985f` 仍是 `planned`。Execution 数量仍是 0。`git status --porcelain -- ase-poc-demo/calculator` 为空。
