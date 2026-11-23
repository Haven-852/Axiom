import asyncio
from contextvars import ContextVar

from ase.agents.runner import AgentRunner
from ase.contracts.domain import AgentRunStatus
from ase.errors import AgentExecutionError
from ase.orchestration.state import AgentWorkflowState

_RUNNER: ContextVar[AgentRunner | None] = ContextVar("ase_planning_runner", default=None)


def bind_planning_runner(runner: AgentRunner):
    return _RUNNER.set(runner)


def reset_planning_runner(token) -> None:
    _RUNNER.reset(token)


def planning_node(state: AgentWorkflowState) -> dict:
    runner = _RUNNER.get()
    if runner is None:
        raise RuntimeError("planning runner is not bound")
    input_data = {"research_artifact_id": state["research_artifact_id"]}
    if state.get("planning_decisions"):
        input_data["decisions"] = state["planning_decisions"]
        input_data["parent_plan_artifact_id"] = state.get("parent_plan_artifact_id") or ""
        input_data["parent_clarifications"] = state.get("parent_clarifications") or []
        input_data["parent_tasks"] = state.get("parent_tasks") or []
        input_data["clarification_revision"] = state.get("clarification_revision") or ""
        input_data["user_change_hash"] = state.get("user_change_hash") or ""
        input_data["deferred_questions"] = state.get("deferred_questions") or []
    result = asyncio.run(
        runner.run(
            workflow_id=state["workflow_id"],
            project_id=state["project_id"] or "",
            agent_name="planning",
            input_data=input_data,
            idempotency_key=state["planning_key"],
        )
    )
    if result.status != AgentRunStatus.COMPLETED:
        raise AgentExecutionError(result.error_message or result.status.value)
    return {
        "current_agent": "planning",
        "agent_run_ids": list(state.get("agent_run_ids") or []) + [result.agent_run_id],
        "artifact_ids": list(state.get("artifact_ids") or []) + list(result.output_artifact_ids),
        "plan": result.output,
        "status": result.status.value,
    }
