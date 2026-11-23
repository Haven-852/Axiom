# Research Agent

Research Agent 是只读研究代理。LangGraph 编排它，Agent Runner 执行它。它不导入 OpenHands，不创建 Execution，不启动 Docker，也不修改仓库。

```text
POST /api/v1/research-workflows
        |
        v
ResearchWorkflowService
        |
        v
LangGraph research graph
        |
        v
Agent Runner
        |
        v
ResearchAgent
        |
        +-- ProjectReader 读取授权仓库
        +-- llm_client.complete 调用 DeepSeek
        |
        v
ResearchReport
        |
        +-- agent_runs
        +-- artifacts.kind = research_report
        +-- model_usages.agent_run_id
```

生产编码图 `src/ase/workflow/graph.py` 没有改执行路径。ResearchStubAgent 和 EchoAgent 仍保留在原来的示例图里。

## 报告

协议在 `src/ase/agents/research/schemas.py`，版本 `1.0`。它和 Phase 1 计划器用的 `RequirementAnalysis` 不是同一个类型。

项目语言、框架和依赖由文件扫描写入 `project_analysis`。模型只负责需求解释、代码发现、风险、验收标准和摘要。代码发现的证据文本必须能在文件里定位，行号会改写到命中的那一行。定位不到的发现会被丢弃，并写入 `limitations`。

## 幂等

指纹由 workflow、需求、Git 修订、Agent 版本和研究选项组成。已完成的同一指纹直接返回原来的 AgentRun。失败、超时和取消不会被当成成功。`running` 的同一行可以继续执行。Artifact 用 `research://{agent_run_id}` 去重。

研究工作流结束时状态是 `researched`。这个状态不占用项目的活动写流程。研究过程不获取 Repository Lock。

## 模型

调用 `src/ase/agents/llm_client.py` 的 `complete()`。优先请求 JSON object。若服务商返回 HTTP 400，再退回普通文本。格式最多再修两次。用量只记录响应里的整数 token。没有成本字段时 `estimated_cost` 为空。
