"""Final check before a coding task may be claimed or executed."""

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ase.contracts.domain import ApprovalStatus, TaskStatus
from ase.db.orm import ExecutionRow, RepositoryLeaseRow, WorkflowRow
from ase.errors import WorkflowConflict
from ase.planning.review_service import plan_hash
from ase.planning.schemas import Plan
from ase.store import get_artifact, get_task, get_workflow, list_artifacts, list_tasks_for_plan

_ACTIVE = {"created", "queued", "starting", "running"}
_CLOSED_WORKFLOW = {"failed", "cancelled"}


class EligibilityError(WorkflowConflict):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def evaluate(
    session: Session,
    *,
    workflow_id: str,
    task_id: str,
    repository_key: str = "",
    allow_claimed: bool = False,
) -> dict:
    workflow = get_workflow(session, workflow_id)
    if workflow is None:
        raise EligibilityError("workflow_missing", "workflow does not exist")
    if str(workflow.status) in _CLOSED_WORKFLOW:
        raise EligibilityError("workflow_closed", f"workflow status is {workflow.status}")
    versions = _versions(session, workflow_id)
    if not versions:
        raise EligibilityError("plan_missing", "workflow has no plan version")
    current = versions[-1]
    plan, artifact_id = _load_plan(session, workflow_id, current)
    actual_hash = plan_hash(plan)
    if current.get("plan_hash") != actual_hash:
        raise EligibilityError("plan_hash_mismatch", "stored plan hash does not match the plan")
    if not plan.validation_result.valid:
        raise EligibilityError("dag_invalid", "plan DAG is not valid")
    task = get_task(session, task_id)
    if task is None:
        raise EligibilityError("task_missing", "task does not exist")
    if task.plan_id != current["plan_id"]:
        raise EligibilityError("task_superseded", "task does not belong to the current plan version")
    plan_task = next((item for item in plan.tasks if item.task_id == task_id), None)
    if plan_task is None:
        raise EligibilityError("task_superseded", "task is not in the current plan")
    blockers = _blockers(plan, plan_task)
    approval = _approval(session, workflow_id, artifact_id, actual_hash)
    if approval is None or approval.status != ApprovalStatus.APPROVED:
        detail = "current plan version is not approved"
        if blockers:
            detail += "; unresolved blocking clarifications: " + ", ".join(blockers)
        raise EligibilityError("plan_not_approved", detail)
    comment = _comment(approval.comment)
    if comment.get("plan_artifact_id") != artifact_id or comment.get("plan_hash") != actual_hash:
        raise EligibilityError("approval_mismatch", "approval is not bound to the current plan artifact and hash")
    if comment.get("plan_version") != current["plan_version"]:
        raise EligibilityError("approval_mismatch", "approval is not bound to the current plan version")
    status = task.status.value if isinstance(task.status, TaskStatus) else str(task.status)
    allowed = {TaskStatus.READY.value, TaskStatus.IN_PROGRESS.value} if allow_claimed else {TaskStatus.READY.value}
    if status not in allowed:
        raise EligibilityError("task_not_ready", f"task status is {status}")
    if blockers:
        raise EligibilityError("blocking_clarifications", "unresolved blocking clarifications: " + ", ".join(blockers))
    tasks = {item.id: item for item in list_tasks_for_plan(session, current["plan_id"])}
    for dependency in plan_task.dependencies:
        predecessor = tasks.get(dependency)
        if predecessor is None or _value(predecessor.status) != TaskStatus.COMPLETED.value:
            raise EligibilityError("dependency_open", f"dependency {dependency} is not completed")
    active = _active_execution(session, workflow_id, task_id)
    if active is not None and not allow_claimed:
        raise EligibilityError("task_running", f"task already has execution {active}")
    if repository_key:
        _assert_lock(session, repository_key, workflow_id)
    return {
        "workflow_id": workflow_id,
        "project_id": workflow.project_id,
        "plan_id": current["plan_id"],
        "plan_version": current["plan_version"],
        "plan_artifact_id": artifact_id,
        "plan_hash": actual_hash,
        "task_id": task_id,
        "task_status": status,
        "approval_id": approval.id,
    }


def lock_workflow(session: Session, workflow_id: str) -> None:
    session.scalar(select(WorkflowRow).where(WorkflowRow.id == workflow_id).with_for_update())


def _versions(session: Session, workflow_id: str) -> list[dict]:
    rows = list_artifacts(session, workflow_id, kind="plan_version")
    records = [json.loads(item.body) for item in rows]
    return sorted(records, key=lambda item: int(item["plan_version"]))


def _load_plan(session: Session, workflow_id: str, version: dict) -> tuple[Plan, str]:
    artifact = get_artifact(session, version["plan_artifact_id"])
    if artifact is None or artifact.workflow_id != workflow_id:
        raise EligibilityError("plan_missing", "plan artifact does not exist")
    plan = Plan.model_validate(json.loads(artifact.body)["content"])
    if plan.plan_id != version["plan_id"]:
        raise EligibilityError("plan_mismatch", "plan artifact does not match the version record")
    return plan, artifact.id


def _approval(session: Session, workflow_id: str, artifact_id: str, plan_hash_value: str):
    from ase.store import get_approval_by_subject

    subject = f"plan-review:{artifact_id}:{plan_hash_value}"
    return get_approval_by_subject(session, workflow_id, subject)


def _comment(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise EligibilityError("approval_mismatch", "approval comment is not plan metadata") from exc
    if not isinstance(value, dict):
        raise EligibilityError("approval_mismatch", "approval comment is not plan metadata")
    return value


def _blockers(plan: Plan, task) -> list[str]:
    watched = set(task.blocking_clarification_ids)
    found = []
    for item in plan.clarification_items:
        if item.clarification_id not in watched and task.task_id not in item.affected_task_ids:
            continue
        if item.blocking and item.status.value in {"unresolved", "deferred"}:
            found.append(item.clarification_id)
    return found


def _active_execution(session: Session, workflow_id: str, task_id: str) -> str | None:
    row = session.scalar(
        select(ExecutionRow.id).where(
            ExecutionRow.workflow_id == workflow_id,
            ExecutionRow.task_id == task_id,
            ExecutionRow.status.in_(_ACTIVE),
        )
    )
    return row


def _assert_lock(session: Session, repository_key: str, workflow_id: str) -> None:
    from ase.contracts.domain import utcnow

    row = session.scalar(select(RepositoryLeaseRow).where(RepositoryLeaseRow.repository_key == repository_key))
    if row is None:
        raise EligibilityError("repository_lock", "repository lock is not held")
    expiry = row.expires_at
    if expiry.tzinfo is None:
        from datetime import timezone

        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= utcnow():
        raise EligibilityError("repository_lock", "repository lock is expired")
    if row.workflow_id != workflow_id:
        raise EligibilityError("repository_lock", "repository lock is held by another workflow")


def _value(status) -> str:
    return status.value if isinstance(status, TaskStatus) else str(status)
