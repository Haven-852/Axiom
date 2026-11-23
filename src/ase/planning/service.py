"""Run planning on an existing workflow. It does not take a repository write lock."""

from ase.agents.planning_agent import PlanningAgent, planning_definition
from ase.agents.registry import AgentRegistry
from ase.agents.runner import AgentRunner
from ase.contracts.domain import AgentRunStatus, WorkflowStatus
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.orchestration.planning_graph import build_planning_graph
from ase.orchestration.planning_nodes import bind_planning_runner, reset_planning_runner
from ase.planning.dag_validator import blocked_task_ids
from ase.planning.research_input import load_planning_input, planning_key
from ase.planning.schemas import Plan
from ase.store import get_agent_run_by_key, get_workflow, update_workflow
from ase.workflow.runtime import AppContext

_ALLOWED = {WorkflowStatus.RUNNING.value, WorkflowStatus.RESEARCHED.value, WorkflowStatus.PLANNED.value}


class PlanningWorkflowService:
    def __init__(self, ctx: AppContext, checkpointer=None, complete_fn=None) -> None:
        self.ctx = ctx
        registry = AgentRegistry()
        registry.register(planning_definition(), lambda: PlanningAgent(ctx.settings, ctx.sessions, complete_fn=complete_fn))
        self.runner = AgentRunner(registry, ctx.sessions, ctx.settings)
        self.graph = build_planning_graph(checkpointer)

    def start(
        self,
        workflow_id: str,
        research_artifact_id: str,
        *,
        decisions: list[dict] | None = None,
        parent_plan_artifact_id: str = "",
        parent_clarifications: list[dict] | None = None,
        parent_tasks: list[dict] | None = None,
        clarification_revision: str = "",
        user_change_hash: str = "",
        deferred_questions: list[str] | None = None,
        thread_suffix: str = "",
    ) -> dict:
        with session_scope(self.ctx.sessions) as session:
            row = get_workflow(session, workflow_id)
            if row is None:
                raise KeyError(workflow_id)
            if row.status not in _ALLOWED:
                raise WorkflowConflict(f"workflow status {row.status} cannot be planned")
            loaded = load_planning_input(session, workflow_id, research_artifact_id)
            project_id = row.project_id
            status = row.status
            key = planning_key(
                loaded,
                clarification_revision=clarification_revision,
                parent_plan_artifact_id=parent_plan_artifact_id,
                user_change_hash=user_change_hash,
            )
            existing = get_agent_run_by_key(session, key)
        if existing is not None and existing.status == AgentRunStatus.COMPLETED and existing.output_snapshot.get("plan_id"):
            plan = Plan.model_validate(existing.output_snapshot)
            with session_scope(self.ctx.sessions) as session:
                update_workflow(session, workflow_id, WorkflowStatus.PLANNED.value)
            return _started(workflow_id, existing.id, (existing.output_artifact_ids or [None])[-1], plan)
        thread = f"{workflow_id}:planning" if not thread_suffix else f"{workflow_id}:planning:{thread_suffix}"
        initial = {
            "workflow_id": workflow_id,
            "project_id": project_id,
            "research_artifact_id": research_artifact_id,
            "planning_key": key,
            "planning_decisions": list(decisions or []),
            "parent_plan_artifact_id": parent_plan_artifact_id,
            "parent_clarifications": list(parent_clarifications or []),
            "parent_tasks": list(parent_tasks or []),
            "clarification_revision": clarification_revision,
            "user_change_hash": user_change_hash,
            "deferred_questions": list(deferred_questions or []),
            "agent_run_ids": [],
            "artifact_ids": [],
            "status": status,
        }
        token = bind_planning_runner(self.runner)
        try:
            final = self.graph.invoke(initial, {"configurable": {"thread_id": thread}})
        finally:
            reset_planning_runner(token)
        with session_scope(self.ctx.sessions) as session:
            update_workflow(session, workflow_id, WorkflowStatus.PLANNED.value)
        plan = Plan.model_validate(final["plan"])
        return _started(workflow_id, final["agent_run_ids"][-1], final["artifact_ids"][-1], plan)


def _started(workflow_id: str, agent_run_id: str | None, artifact_id: str | None, plan: Plan) -> dict:
    return {
        "workflow_id": workflow_id,
        "status": WorkflowStatus.PLANNED.value,
        "agent_run_id": agent_run_id,
        "plan_artifact_id": artifact_id,
        "plan_id": plan.plan_id,
        "task_ids": [task.task_id for task in plan.tasks],
        "blocked_task_ids": blocked_task_ids(plan),
        "clarification_count": len(plan.clarification_items),
    }
