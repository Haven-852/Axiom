# Phase 2.1 验收报告

日期：2026-11-23。阶段名称：Research Agent Implementation。

Phase 2.1 完成后停止。没有开发 Planning Agent，也没有修改 OpenHands Adapter 和生产编码图的执行机制。

## 1. Research Agent 架构

LangGraph 研究图只有一个 `research` 节点。节点调用 Agent Runner，Runner 调用 `ResearchAgent`。模型调用走 `llm_client.complete()`。项目事实来自 `ProjectReader`。

研究完成把 Workflow 标为 `researched`。这个状态不阻止之后的编码工作流。研究不获取 Repository Lock，不创建 Execution。

## 2. ResearchReport Schema

`src/ase/agents/research/schemas.py`，`schema_version` 固定为 `1.0`。包含需求、项目、代码发现、风险、验收标准、摘要和限制。额外字段会被拒绝。技术栈列表由文件扫描覆盖，不接受模型自由发挥。

## 3. ProjectReader

只读、单项目、相对路径。拒绝路径穿越、越界符号链接、敏感文件和超大文件。目录深度、文件数和搜索条数有上限。Git 只读修订、状态和 diff 统计。

## 4. DeepSeek 调用

`complete()` 增加可选的 JSON object 模式。HTTP 400 时退回普通文本。Schema 不合格最多再请求两次。密钥不进入 Prompt、Artifact 或日志。

## 5. AgentRun

真实运行：

- workflow `1dd7c84d-36d1-4dcb-a66e-1004e0a1985f`
- agent_run `c36ae47a-eaf4-43d7-8c1d-d11af093eb9e`
- 名称 `research`，版本 `1`
- 状态 `completed`
- 输入快照是需求文本，输出快照是 ResearchReport

同一指纹的已完成记录会直接复用。失败记录不会被当成成功。`running` 记录可以在同一行上继续。

## 6. Artifact

- id `450e2050-cd34-4dcc-8766-fec43e63fc82`
- kind `research_report`
- 正文包含 schema_version、agent_run_id、revision、files_read、search_results 和报告 JSON
- 关联在 `agent_runs.output_artifact_ids`
- 没有新建表

## 7. ModelUsage

一条真实记录：

| 字段 | 值 |
| --- | --- |
| provider | deepseek |
| model | deepseek-flash |
| prompt_tokens | 2000 |
| completion_tokens | 3957 |
| total_tokens | 5957 |
| latency_seconds | 22.125 |
| estimated_cost | null |
| agent_run_id | c36ae47a-eaf4-43d7-8c1d-d11af093eb9e |

成本为空，因为响应里没有成本数字。没有编造 token。

## 8. LangGraph

真实运行先在 `research` 节点前停下，PostgreSQL checkpoint 已有该 thread。恢复后才调用模型。失败时节点抛出异常，Workflow 标为 `failed`。

## 9. 幂等与恢复

指纹包含 workflow、需求、修订、Agent 版本和选项。输入变化会生成新的 AgentRun。恢复未完成运行时，相同 artifact uri 不重复插入。语义仍是 at-least-once。

## 10. 文件证据

仓库修订 `54a0303761a57d1e572c8304881975134b3b594a`。读取 3 个文件，搜索命中 3 条。验收报告中的发现：

- `calculator.py:5` evaluate 仍是未实现函数
- `calculator.py:1` add 已实现
- `test_calculator.py:10` 现有测试只断言未实现
- `README.md:3` 写明要补科学计数法

行号由证据文本在文件中的位置确定。运行前后文件哈希和 `git status` 都没有变化。

## 11. 测试

非真实模型回归 51 项通过，0 失败。命令排除了 Phase 1.1/1.2.1 的真实编码测试和本阶段的真实研究测试。

另有 `tests/test_phase21_live.py` 1 项通过。

| 证据 | 结果 |
| --- | --- |
| Level 1 | 真实 PostgreSQL、真实 DeepSeek、真实 calculator 仓库、真实 checkpoint 恢复。1 项通过 |
| Level 3 | Scripted 模型、SQLite、研究图中断恢复、API |
| Level 4 | 本阶段研究测试没有用 TestLLM 代替 DeepSeek。OpenHands SDK 的 TestLLM 仍只属于原有适配器回归 |

没有重跑真实编码崩溃、Agent Server 重启、双模型和 OpenHands Conversation 恢复。研究改动没有进入这些路径。

## 12. 已知风险

- 证据发送给模型前会截断。模型自己的 limitations 也写了摘录不完整。
- 搜索没有命中时只采样少量源文件，并在 limitations 标明。
- 格式修复会产生额外的模型调用和 token。
- 已发出的 DeepSeek HTTP 不能撤回。
- `estimated_cost` 依赖服务商返回，当前为 null。
- 研究 Workflow 在运行期间仍占用项目的活动名额，完成后才释放。

## 13. Planning Agent 应如何消费

读取 Artifact 正文里的 `content`。用 `code_findings` 的相对路径和行号定位代码。`missing_capabilities` 和 `priority=must` 的验收标准进入任务。`source=inferred` 的标准必须保持推断标记。`clarification_questions` 没有回答前，不要把语法范围写成确定任务。Planning 仍不调用 OpenHands，也不创建 Execution。
