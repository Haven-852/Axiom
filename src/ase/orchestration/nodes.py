import asyncio

from ase.agents.runner import AgentRunner
from ase.errors import AgentExecutionError
from ase.orchestration.state import AgentWorkflowState

_RUNNER: AgentRunner | None = None


def bind_runner(runner: AgentRunner) -> None:
    global _RUNNER
    _RUNNER = runner


def _runner() -> AgentRunner:
    if _RUNNER is None:
        raise RuntimeError("agent runner is not bound")
    return _RUNNER


def research_node(state: AgentWorkflowState) -> dict:
    result = asyncio.run(_runner().run(
        workflow_id=state["workflow_id"],
        project_id=state["project_id"] or "",
        agent_name="research_stub",
        input_data={"requirement": state["requirement"]},
        idempotency_key=f"{state['workflow_id']}:research_stub:1",
    ))
    if result.status.value != "completed":
        raise AgentExecutionError(result.error_message or result.status.value)
    errors = list(state.get("errors") or [])
    return {
        "current_agent": "research_stub",
        "agent_run_ids": list(state.get("agent_run_ids") or []) + [result.agent_run_id],
        "research_output": result.output,
        "errors": errors,
        "status": result.status.value,
    }


def echo_node(state: AgentWorkflowState) -> dict:
    summary = (state.get("research_output") or {}).get("summary") or ""
    result = asyncio.run(_runner().run(
        workflow_id=state["workflow_id"],
        project_id=state["project_id"] or "",
        agent_name="echo",
        input_data={"text": summary},
        idempotency_key=f"{state['workflow_id']}:echo:1",
    ))
    if result.status.value != "completed":
        raise AgentExecutionError(result.error_message or result.status.value)
    errors = list(state.get("errors") or [])
    return {
        "current_agent": "echo",
        "agent_run_ids": list(state.get("agent_run_ids") or []) + [result.agent_run_id],
        "artifact_ids": list(state.get("artifact_ids") or []) + list(result.output_artifact_ids),
        "echo_output": result.output,
        "errors": errors,
        "status": result.status.value,
    }
