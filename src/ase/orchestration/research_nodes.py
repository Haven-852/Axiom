import asyncio
from contextvars import ContextVar

from ase.agents.runner import AgentRunner
from ase.contracts.domain import AgentRunStatus
from ase.errors import AgentExecutionError
from ase.orchestration.state import AgentWorkflowState

_RUNNER: ContextVar[AgentRunner | None] = ContextVar("ase_research_runner", default=None)


def bind_research_runner(runner: AgentRunner):
    return _RUNNER.set(runner)


def reset_research_runner(token) -> None:
    _RUNNER.reset(token)


def research_report_node(state: AgentWorkflowState) -> dict:
    runner = _RUNNER.get()
    if runner is None:
        raise RuntimeError("research runner is not bound")
    result = asyncio.run(
        runner.run(
            workflow_id=state["workflow_id"],
            project_id=state["project_id"] or "",
            agent_name="research",
            input_data={
                "requirement": state["requirement"],
                "include_tests": state.get("include_tests", True),
                "include_dependencies": state.get("include_dependencies", True),
                "max_files": state.get("max_files", 100),
                "max_context_chars": state.get("max_context_chars", 50000),
            },
            idempotency_key=state["research_key"],
        )
    )
    if result.status != AgentRunStatus.COMPLETED:
        raise AgentExecutionError(result.error_message or result.status.value)
    return {
        "current_agent": "research",
        "agent_run_ids": list(state.get("agent_run_ids") or []) + [result.agent_run_id],
        "artifact_ids": list(state.get("artifact_ids") or []) + list(result.output_artifact_ids),
        "research_output": result.output,
        "research_report": result.output,
        "status": result.status.value,
    }
