# ADR-014 Research Agent

状态：已接受。

Research Agent 通过现有 Agent Runner 执行，使用 `llm_client.complete()` 调用 DeepSeek。OpenHands 继续只服务编码运行时。

项目技术栈由 ProjectReader 从仓库文件得出。模型输出必须通过 ResearchReport schema。代码发现的证据必须能在相对路径指向的文件中找到。

研究报告写入已有 `artifacts` 表，`kind` 为 `research_report`。`agent_run_id`、修订、读取文件数和搜索条数放在正文 JSON 里，并用 `agent_runs.output_artifact_ids` 关联。不新增表，也不创建 Execution。

研究工作流使用独立的 LangGraph 图和现有 checkpoint。完成状态是 `researched`，不获取仓库写锁。
