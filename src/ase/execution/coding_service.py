"""Explicit coding entry. It does not resume the production coding graph."""

import asyncio
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

from ase.contracts.domain import TaskStatus
from ase.db.session import session_scope
from ase.execution.coding_context import build_instruction
from ase.execution.eligibility import EligibilityError, evaluate, lock_workflow
from ase.execution.task_completion import assess
from ase.execution.task_scheduler import select_next
from ase.infra.policy import assert_workspace_allowed
from ase.infra.repository_lock import acquire_repository_lease, repository_identity
from ase.planning.schemas import Plan
from ase.schemas.coding import CodingInput, CodingResult
from ase.store import (
    audit,
    get_artifact,
    get_task,
    get_workflow,
    list_artifacts,
    list_executions_for_workflow,
    list_tasks_for_plan,
    set_task_status,
)
from ase.workflow.runtime import AppContext

_ACTIVE = {"created", "queued", "starting", "running"}


class CodingTaskService:
    def __init__(self, ctx: AppContext, runner, checkpointer=None) -> None:
        self.ctx = ctx
        self.runner = runner
        self.checkpointer = checkpointer
        self._inside = False

    def eligibility(self, workflow_id: str, task_id: str, repository_key: str = "") -> dict:
        with session_scope(self.ctx.sessions) as session:
            return evaluate(session, workflow_id=workflow_id, task_id=task_id, repository_key=repository_key)

    def next_task(self, workflow_id: str) -> str | None:
        with session_scope(self.ctx.sessions) as session:
            plan = _current_plan(session, workflow_id)
            rows = list_tasks_for_plan(session, plan.plan_id)
            status = {item.id: item.status.value if isinstance(item.status, TaskStatus) else str(item.status) for item in rows}
            active = {
                item.task_id
                for item in list_executions_for_workflow(session, workflow_id)
                if item.status.value in _ACTIVE
            }
        return select_next(plan, status, active)

    def start_task(self, workflow_id: str, task_id: str, workspace_path: str, repository_revision: str = "") -> dict:
        if self._inside:
            return self._execute(workflow_id, task_id, workspace_path, repository_revision)
        from langgraph.checkpoint.memory import MemorySaver

        from ase.orchestration.coding_graph import build_coding_graph

        self._inside = True
        try:
            graph = build_coding_graph(self, self.checkpointer or MemorySaver())
            final = graph.invoke(
                {
                    "workflow_id": workflow_id,
                    "task_id": task_id,
                    "workspace_path": workspace_path,
                    "repository_revision": repository_revision,
                },
                {"configurable": {"thread_id": f"{workflow_id}:coding:{task_id}"}},
            )
        finally:
            self._inside = False
        if final.get("error"):
            raise EligibilityError("no_ready_task", final["error"])
        return final["result"]

    def _execute(self, workflow_id: str, task_id: str, workspace_path: str, repository_revision: str = "") -> dict:
        workspace = assert_workspace_allowed(Path(workspace_path), self.ctx.settings)
        repository_key = _repository_key(workspace)
        with session_scope(self.ctx.sessions) as session:
            lock_workflow(session, workflow_id)
            if repository_key:
                workflow = get_workflow(session, workflow_id)
                acquire_repository_lease(
                    session,
                    repository_key=repository_key,
                    holder_id=workflow_id,
                    workflow_id=workflow_id,
                    project_id=workflow.project_id,
                    ttl_seconds=self.ctx.settings.repository_lease_seconds,
                )
            decision = evaluate(
                session,
                workflow_id=workflow_id,
                task_id=task_id,
                repository_key=repository_key,
                allow_claimed=True,
            )
            task = get_task(session, task_id)
            status = task.status.value if isinstance(task.status, TaskStatus) else str(task.status)
            if status == TaskStatus.READY.value:
                fresh = evaluate(session, workflow_id=workflow_id, task_id=task_id, repository_key=repository_key)
                set_task_status(session, task_id, TaskStatus.IN_PROGRESS.value)
                decision = fresh
            elif status != TaskStatus.IN_PROGRESS.value:
                raise EligibilityError("task_not_ready", f"task status is {status}")
            payload = _input(session, decision, workspace, repository_revision)
            audit(session, "api-key", "coding_claim", task_id, {"plan_id": decision["plan_id"], "plan_hash": decision["plan_hash"]})
        key = _fingerprint(payload)
        connection = self.ctx.sessions.kw["bind"].connect()
        lock_id = int(hashlib.sha256(f"{workflow_id}:{task_id}".encode()).hexdigest()[:15], 16)
        locked = False
        try:
            if connection.dialect.name == "postgresql":
                locked = bool(connection.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id}).scalar())
                if not locked:
                    raise EligibilityError("task_running", "task execution is already in progress")
            result = asyncio.run(
                self.runner.run(
                    workflow_id=workflow_id,
                    project_id=decision["project_id"],
                    agent_name="coding",
                    input_data=payload.model_dump(),
                    task_id=task_id,
                    idempotency_key=key,
                )
            )
        finally:
            if locked:
                connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
                connection.commit()
            connection.close()
        return self._finish(workflow_id, task_id, result)

    def start_next(self, workflow_id: str, workspace_path: str, repository_revision: str = "") -> dict:
        task_id = self.next_task(workflow_id)
        if task_id is None:
            raise EligibilityError("no_ready_task", "no approved task is ready")
        return self.start_task(workflow_id, task_id, workspace_path, repository_revision)

    def complete_task(self, workflow_id: str, task_id: str, execution_id: str) -> dict:
        with session_scope(self.ctx.sessions) as session:
            gate = assess(session, workflow_id=workflow_id, execution_id=execution_id, execution_status="completed")
            if not gate["complete"]:
                raise EligibilityError("task_unverified", gate["reason"] or "task is not verified")
            set_task_status(session, task_id, TaskStatus.COMPLETED.value)
        return gate

    def _finish(self, workflow_id: str, task_id: str, result) -> dict:
        execution_id = result.execution_ids[0] if result.execution_ids else ""
        output = result.output or {}
        with session_scope(self.ctx.sessions) as session:
            execution_status = str(output.get("execution_status") or "")
            gate = assess(session, workflow_id=workflow_id, execution_id=execution_id, execution_status=execution_status) if execution_id else {
                "complete": False,
                "test_status": "unverified",
                "test_result_ids": [],
                "reason": "execution was not created",
            }
            if gate["complete"]:
                set_task_status(session, task_id, TaskStatus.COMPLETED.value)
            elif execution_status in {"failed", "timeout", "cancelled"}:
                set_task_status(session, task_id, TaskStatus.FAILED.value)
            coded = CodingResult(
                task_id=task_id,
                execution_id=execution_id,
                agent_run_id=result.agent_run_id,
                status=result.status.value,
                summary=str(output.get("summary") or ""),
                test_status=gate["test_status"],
                test_result_ids=gate["test_result_ids"],
                failure_reason=gate["reason"] or str(result.error_message or ""),
                unresolved_issues=[gate["reason"]] if gate["reason"] else [],
            )
        return coded.model_dump()


def _current_plan(session, workflow_id: str) -> Plan:
    rows = list_artifacts(session, workflow_id, kind="plan_version")
    if not rows:
        raise EligibilityError("plan_missing", "workflow has no plan version")
    current = sorted((json.loads(item.body) for item in rows), key=lambda item: int(item["plan_version"]))[-1]
    artifact = get_artifact(session, current["plan_artifact_id"])
    if artifact is None:
        raise EligibilityError("plan_missing", "plan artifact does not exist")
    return Plan.model_validate(json.loads(artifact.body)["content"])


def _input(session, decision: dict, workspace: Path, repository_revision: str) -> CodingInput:
    artifact = get_artifact(session, decision["plan_artifact_id"])
    plan = Plan.model_validate(json.loads(artifact.body)["content"])
    task = next(item for item in plan.tasks if item.task_id == decision["task_id"])
    decisions = [
        {"question": item.question, "answer": item.answer, "source": "user"}
        for item in plan.clarification_items
        if item.status.value == "answered" and item.answer
    ]
    payload = CodingInput(
        workflow_id=decision["workflow_id"],
        plan_id=decision["plan_id"],
        plan_version=int(decision["plan_version"]),
        plan_artifact_id=decision["plan_artifact_id"],
        plan_hash=decision["plan_hash"],
        task_id=task.task_id,
        task_description=task.description,
        task_type=task.task_type.value,
        priority=task.priority.value,
        acceptance_criteria=list(task.acceptance_criteria),
        related_files=list(task.related_files),
        source_findings=list(task.source_findings),
        source_research_artifact_id=plan.source_research_artifact_id,
        dependencies=list(task.dependencies),
        repository_revision=repository_revision,
        instruction="",
        workspace_path=str(workspace),
        user_decisions=decisions,
    )
    payload.instruction = build_instruction(payload, workspace)
    return payload


def _fingerprint(payload: CodingInput) -> str:
    body = payload.model_dump()
    body.pop("instruction", None)
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return f"coding:{payload.task_id}:{digest[:48]}"


def _repository_key(workspace: Path) -> str:
    try:
        return repository_identity(workspace)
    except RuntimeError:
        return ""
