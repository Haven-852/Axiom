"""Persist a validated plan with the existing task tables."""

from sqlalchemy.exc import IntegrityError

from ase.contracts.domain import DevelopmentPlan, Task, TaskDependency, TaskStatus, new_id
from ase.db.orm import DevelopmentPlanRow
from ase.db.session import session_scope
from ase.planning.dag_validator import blocked_task_ids
from ase.planning.schemas import Plan
from ase.store import save_plan


def persist_plan(sessions, requirement_id: str, plan: Plan, *, fail: bool = False, hold_for_approval: bool = False) -> None:
    try:
        _insert(sessions, requirement_id, plan, fail=fail, hold_for_approval=hold_for_approval)
    except IntegrityError:
        return


def _insert(sessions, requirement_id: str, plan: Plan, *, fail: bool, hold_for_approval: bool = False) -> None:
    with session_scope(sessions) as session:
        if session.get(DevelopmentPlanRow, plan.plan_id) is not None:
            return
        blocked = set(blocked_task_ids(plan))
        tasks = [
            Task(
                id=task.task_id,
                plan_id=plan.plan_id,
                title=task.title,
                instruction=task.description,
                status=TaskStatus.BLOCKED
                if task.task_id in blocked or (hold_for_approval and task.task_type.value in {"implementation", "testing", "refactoring"})
                else TaskStatus.READY,
                agent_id=task.task_type.value,
            )
            for task in plan.tasks
        ]
        dependencies = []
        for task in plan.tasks:
            for predecessor in task.dependencies:
                dependencies.append(
                    TaskDependency(
                        id=new_id(),
                        plan_id=plan.plan_id,
                        predecessor_id=predecessor,
                        successor_id=task.task_id,
                    )
                )
        save_plan(
            session,
            DevelopmentPlan(
                id=plan.plan_id,
                requirement_id=requirement_id,
                summary=plan.planning_summary,
                tasks=tasks,
                dependencies=dependencies,
            ),
        )
        if fail:
            raise RuntimeError("persist failed")
