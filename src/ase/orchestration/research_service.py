"""Starts the read-only research workflow. It does not take a repository write lease."""

from ase.agents.registry import AgentRegistry
from ase.agents.research.agent import ResearchAgent, research_definition
from ase.agents.research.services import research_key, research_options
from ase.agents.runner import AgentRunner
from ase.contracts.domain import Requirement, WorkflowStatus, new_id
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.orchestration.research_graph import build_research_graph
from ase.orchestration.research_nodes import bind_research_runner, reset_research_runner
from ase.store import active_workflow_id, get_agent_run, get_project, save_requirement, save_workflow, update_workflow
from ase.workflow.runtime import AppContext


class ResearchWorkflowService:
    def __init__(self, ctx: AppContext, checkpointer=None, complete_fn=None) -> None:
        self.ctx = ctx
        registry = AgentRegistry()
        registry.register(research_definition(), lambda: ResearchAgent(ctx.settings, ctx.sessions, complete_fn=complete_fn))
        self.runner = AgentRunner(registry, ctx.sessions, ctx.settings)
        self.graph = build_research_graph(checkpointer)
        self._checkpointer = checkpointer

    def start(self, project_id: str, requirement: str, options: dict | None = None) -> dict:
        selected = research_options(options or {})
        workflow_id = new_id()
        with session_scope(self.ctx.sessions) as session:
            project = get_project(session, project_id)
            if project is None:
                raise KeyError(project_id)
            active = active_workflow_id(session, project_id)
            if active is not None:
                raise WorkflowConflict(f"project already has active workflow {active}")
            requirement_row = Requirement(project_id=project_id, title="research", statement=requirement)
            save_requirement(session, requirement_row)
            save_workflow(session, workflow_id, project_id, requirement_row.id, WorkflowStatus.RUNNING.value)
            repo_path = project.repo_path
        revision = _revision(repo_path)
        key = research_key(workflow_id, requirement, revision, "1", selected)
        initial = {
            "workflow_id": workflow_id,
            "project_id": project_id,
            "requirement": requirement,
            "research_key": key,
            "agent_run_ids": [],
            "artifact_ids": [],
            "status": WorkflowStatus.RUNNING.value,
            **selected,
        }
        token = bind_research_runner(self.runner)
        try:
            final = self.graph.invoke(initial, {"configurable": {"thread_id": workflow_id}})
        except Exception as exc:
            with session_scope(self.ctx.sessions) as session:
                update_workflow(session, workflow_id, WorkflowStatus.FAILED.value, type(exc).__name__)
            raise
        finally:
            reset_research_runner(token)
        with session_scope(self.ctx.sessions) as session:
            update_workflow(session, workflow_id, WorkflowStatus.RESEARCHED.value)
        artifact_id = (final.get("artifact_ids") or [None])[-1]
        agent_run_id = (final.get("agent_run_ids") or [None])[-1]
        return {
            "workflow_id": workflow_id,
            "status": WorkflowStatus.RESEARCHED.value,
            "agent_run_id": agent_run_id,
            "research_artifact_id": artifact_id,
        }

    def status(self, workflow_id: str) -> dict:
        with session_scope(self.ctx.sessions) as session:
            from ase.store import get_workflow

            row = get_workflow(session, workflow_id)
            if row is None:
                raise KeyError(workflow_id)
            values = {}
            try:
                values = self.graph.get_state({"configurable": {"thread_id": workflow_id}}).values or {}
            except Exception:
                values = {}
            run_id = (values.get("agent_run_ids") or [None])[-1]
            artifact_id = (values.get("artifact_ids") or [None])[-1]
            if run_id is not None:
                stored = get_agent_run(session, run_id)
                if stored is not None and stored.output_artifact_ids:
                    artifact_id = stored.output_artifact_ids[-1]
            return {
                "workflow_id": workflow_id,
                "status": row.status,
                "agent_run_id": run_id,
                "research_artifact_id": artifact_id,
            }


def _revision(repo_path: str) -> str:
    import subprocess

    completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        return "unversioned"
    return completed.stdout.strip() or "unversioned"
