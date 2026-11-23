"""Pick one ready task from a validated DAG. Priority never skips a dependency."""

from ase.contracts.domain import TaskStatus
from ase.execution.eligibility import EligibilityError
from ase.planning.schemas import Plan

_PRIORITY = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def select_next(plan: Plan, task_status: dict[str, str], active_task_ids: set[str]) -> str | None:
    if not plan.validation_result.valid:
        raise EligibilityError("dag_invalid", "plan DAG is not valid")
    order = list(plan.validation_result.topological_order)
    known = {task.task_id for task in plan.tasks}
    if set(order) != known:
        raise EligibilityError("dag_invalid", "topological order does not match the plan tasks")
    by_id = {task.task_id: task for task in plan.tasks}
    depth: dict[str, int] = {}
    for task_id in order:
        task = by_id[task_id]
        depth[task_id] = 0 if not task.dependencies else 1 + max(depth[item] for item in task.dependencies)
    completed = {task_id for task_id, status in task_status.items() if status == TaskStatus.COMPLETED.value}
    candidates = []
    for index, task_id in enumerate(order):
        task = by_id[task_id]
        if task_status.get(task_id) != TaskStatus.READY.value:
            continue
        if task_id in active_task_ids:
            continue
        if any(item not in completed for item in task.dependencies):
            continue
        if _blocked(plan, task):
            continue
        candidates.append((depth[task_id], _PRIORITY.get(task.priority.value, 9), index, task_id))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][3]


def _blocked(plan: Plan, task) -> bool:
    watched = set(task.blocking_clarification_ids)
    for item in plan.clarification_items:
        if item.clarification_id not in watched and task.task_id not in item.affected_task_ids:
            continue
        if item.blocking and item.status.value in {"unresolved", "deferred"}:
            return True
    return False
