"""PostgreSQL checks for the existing task tables. No migration and no model call."""

import json

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from ase.app import build_context
from ase.config import Settings
from ase.contracts.domain import DevelopmentPlan, Task, TaskDependency, TaskStatus, new_id
from ase.db.orm import DevelopmentPlanRow, TaskDependencyRow
from ase.db.session import session_scope
from ase.planning.service import PlanningWorkflowService
from ase.store import save_plan, save_project, save_requirement, save_workflow
from ase.contracts.domain import Project, Requirement
from tests.test_phase22 import ScriptModel, _draft, _report, _seed

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"


def _postgres():
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError as exc:
        pytest.skip(f"postgres is not reachable: {exc.__class__.__name__}")
    return engine


def test_task_dependency_foreign_key_and_scripted_plan() -> None:
    _postgres()
    settings = Settings(database_url=URL, coding_runtime="scripted", llm_api_key="postgres-test-key")
    ctx = build_context(settings)
    project = Project(name=f"plan-pg-{new_id()[:8]}", repo_path="E:/unused", base_ref="HEAD")
    requirement = Requirement(project_id=project.id, title="plan", statement="scientific notation")
    workflow_id = new_id()
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
        save_workflow(session, workflow_id, project.id, requirement.id, "researched")
    _run, artifact = _seed(ctx, project, workflow_id, _report())
    started = PlanningWorkflowService(ctx, complete_fn=ScriptModel([_draft()])).start(workflow_id, artifact.id)
    with session_scope(ctx.sessions) as session:
        edges = session.scalar(select(func.count()).select_from(TaskDependencyRow).where(TaskDependencyRow.plan_id == started["plan_id"]))
    assert edges == 1
    with pytest.raises(IntegrityError):
        with session_scope(ctx.sessions) as session:
            save_plan(
                session,
                DevelopmentPlan(
                    id=new_id(),
                    requirement_id=requirement.id,
                    summary="dangling",
                    tasks=[
                        Task(
                            id=new_id(),
                            plan_id="ignored",
                            title="one",
                            instruction="one",
                            status=TaskStatus.READY,
                            agent_id="documentation",
                        )
                    ],
                    dependencies=[
                        TaskDependency(
                            plan_id="ignored",
                            predecessor_id=new_id(),
                            successor_id=new_id(),
                        )
                    ],
                ),
            )
    with session_scope(ctx.sessions) as session:
        dangling = session.scalar(select(func.count()).select_from(DevelopmentPlanRow).where(DevelopmentPlanRow.summary == "dangling"))
    assert dangling == 0
    assert json.loads(json.dumps(started))["status"] == "planned"
