import asyncio
from pathlib import Path

from sqlalchemy import text

from langgraph.types import Command

from ase.contracts.domain import EventSource, ExecutionEvent, WorkflowStatus, new_id
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.execution.service import ExecutionService
from ase.infra.repository_lock import acquire_repository_lease, repository_identity
from ase.store import (
    active_workflow_id,
    get_approval,
    get_project,
    get_workflow,
    list_artifacts,
    list_events,
    list_executions_for_workflow,
    save_event,
    save_workflow,
)
from ase.workflow.runtime import AppContext, bind_context, reset_context


class WorkflowService:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx

    def start(self, project_id: str, requirement_id: str, statement: str) -> dict:
        workflow_id = new_id()
        with session_scope(self.ctx.sessions) as session:
            bind = session.get_bind()
            if bind is not None and bind.dialect.name == "postgresql":
                session.execute(text("SELECT id FROM projects WHERE id = :id FOR UPDATE"), {"id": project_id})
            active = active_workflow_id(session, project_id)
            if active is not None:
                raise WorkflowConflict(f"project already has active workflow {active}")
            project = get_project(session, project_id)
            if project is None:
                raise KeyError(project_id)
            acquire_repository_lease(
                session,
                repository_key=repository_identity(Path(project.repo_path)),
                holder_id=workflow_id,
                workflow_id=workflow_id,
                project_id=project_id,
                ttl_seconds=self.ctx.settings.repository_lease_seconds,
            )
            save_workflow(session, workflow_id, project_id, requirement_id, WorkflowStatus.RUNNING.value)
            save_event(
                session,
                ExecutionEvent(
                    project_id=project_id,
                    workflow_id=workflow_id,
                    event_type="Workflow Started",
                    source=EventSource.ASE,
                    payload={"requirement_id": requirement_id},
                ),
            )
        initial = {
            "project_id": project_id,
            "requirement_id": requirement_id,
            "workflow_id": workflow_id,
            "statement": statement,
            "repair_count": 0,
            "max_repairs": self.ctx.settings.max_repair_attempts,
            "status": WorkflowStatus.RUNNING.value,
        }
        self._invoke(initial, workflow_id)
        return self.snapshot(workflow_id)

    def resume(self, workflow_id: str, decision: str, comment: str) -> dict:
        with session_scope(self.ctx.sessions) as session:
            row = get_workflow(session, workflow_id)
            plan_managed = any(item.kind == "plan_version" for item in list_artifacts(session, workflow_id))
        if row is not None and row.status == WorkflowStatus.CANCELLED.value:
            raise WorkflowConflict("workflow is cancelled")
        if plan_managed:
            raise WorkflowConflict("plan-managed workflow uses the coding task entry, not the legacy coding graph")
        self._invoke(Command(resume={"decision": decision, "comment": comment}), workflow_id)
        return self.snapshot(workflow_id)

    def cancel(self, workflow_id: str) -> dict:
        with session_scope(self.ctx.sessions) as session:
            row = get_workflow(session, workflow_id)
            if row is None:
                raise KeyError(workflow_id)
            finished = {
                WorkflowStatus.DELIVERED.value,
                WorkflowStatus.FAILED.value,
                WorkflowStatus.CANCELLED.value,
                WorkflowStatus.RESEARCHED.value,
                WorkflowStatus.PLANNED.value,
            }
            if row.status not in finished:
                from ase.store import update_workflow

                update_workflow(session, workflow_id, WorkflowStatus.CANCELLED.value, "cancelled")
                save_event(
                    session,
                    ExecutionEvent(
                        project_id=row.project_id,
                        workflow_id=workflow_id,
                        event_type="Workflow Cancelled",
                        source=EventSource.ASE,
                        payload={"previous_status": row.status},
                    ),
                )
            executions = list_executions_for_workflow(session, workflow_id)
        service = ExecutionService(self.ctx.coding, self.ctx.sessions)
        for item in executions:
            asyncio.run(service.propagate_cancel(item.session_id, item.container_id, self.ctx.containers))
        return self.snapshot(workflow_id)

    def continue_unfinished(self, workflow_id: str) -> dict:
        config = {"configurable": {"thread_id": workflow_id}}
        state = self.ctx.graph.get_state(config)
        if not state.next or "human_approval" in state.next:
            return self.snapshot(workflow_id)
        self._invoke(None, workflow_id)
        return self.snapshot(workflow_id)

    def snapshot(self, workflow_id: str) -> dict:
        config = {"configurable": {"thread_id": workflow_id}}
        with session_scope(self.ctx.sessions) as session:
            row = get_workflow(session, workflow_id)
            if row is None:
                raise KeyError(workflow_id)
            events = list_events(session, workflow_id)
        state = self.ctx.graph.get_state(config)
        values = dict(state.values or {})
        with session_scope(self.ctx.sessions) as session:
            approval = None
            if values.get("approval_id"):
                approval = get_approval(session, values["approval_id"])
        return {
            "workflow_id": workflow_id,
            "status": row.status if row else values.get("status"),
            "next": list(state.next),
            "approval_id": None if approval is None else approval.id,
            "approval_status": None if approval is None else approval.status.value,
            "values": _public_values(values),
            "events": [event.model_dump(mode="json") for event in events],
        }

    def _invoke(self, payload, workflow_id: str) -> None:
        token = bind_context(self.ctx)
        try:
            self.ctx.graph.invoke(payload, {"configurable": {"thread_id": workflow_id}})
        finally:
            self.ctx.containers.stop_all()
            reset_context(token)


def _public_values(values: dict) -> dict:
    hidden = {"statement"}
    return {key: value for key, value in values.items() if key not in hidden}
