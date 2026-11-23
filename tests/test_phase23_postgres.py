"""PostgreSQL checks for plan review. Scripted model only."""

import os
import subprocess
import sys
import threading

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError

from ase.app import build_context
from ase.config import Settings
from ase.contracts.domain import Approval, ApprovalStatus, new_id
from ase.db.orm import ApprovalRow
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.planning.review_service import PlanReviewService
from ase.planning.service import PlanningWorkflowService
from ase.store import get_approval, save_approval
from langgraph.checkpoint.memory import MemorySaver
from tests.test_phase22 import ScriptModel, _draft, _report, _seed, _workflow

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"


def _postgres():
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError as exc:
        pytest.skip(f"postgres is not reachable: {exc.__class__.__name__}")
    return engine


def _upgrade() -> None:
    env = os.environ.copy()
    env["ASE_DATABASE_URL"] = URL
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env, cwd=os.path.dirname(os.path.dirname(__file__)))


def test_two_approval_subjects_and_scripted_review_survive_a_new_session() -> None:
    _postgres()
    _upgrade()
    engine = create_engine(URL, pool_pre_ping=True)
    with engine.connect() as connection:
        names = connection.execute(
            text("select conname from pg_constraint where conrelid = 'approvals'::regclass and contype = 'u'")
        ).scalars().all()
    assert "uq_approvals_workflow_subject" in names
    settings = Settings(database_url=URL, coding_runtime="scripted", llm_api_key="postgres-test-key")
    ctx = build_context(settings)
    project_ctx, _project, _requirement, workflow_id = _workflow(settings, status="researched")
    _run, artifact = _seed(project_ctx, _project, workflow_id, _report(["科学计数法只支持字面量，还是完整表达式？"]))
    model = ScriptModel([_draft(), _draft()])
    service = PlanReviewService(PlanningWorkflowService(project_ctx, checkpointer=MemorySaver(), complete_fn=model))
    service.planning.start(workflow_id, artifact.id)
    item = service.list_clarifications(workflow_id)[0]
    service.answer(workflow_id, item["clarification_id"], "完整数学表达式")
    revised = service.revise(workflow_id)
    approved = service.decide(workflow_id, "approved", "确认", revised["plan_artifact_id"], revised["plan_hash"])
    restarted = build_context(settings)
    with session_scope(restarted.sessions) as session:
        stored = get_approval(session, approved["approval_id"])
        assert stored is not None
        assert stored.status == ApprovalStatus.APPROVED
        assert stored.subject.startswith("plan-review:")
    again = PlanReviewService(PlanningWorkflowService(restarted, checkpointer=MemorySaver(), complete_fn=model))
    versions = again.list_versions(workflow_id)
    assert versions[-1]["plan_version"] == revised["plan_version"]
    assert again.history(workflow_id, item["clarification_id"])[0]["answer"] == "完整数学表达式"
    with session_scope(ctx.sessions) as session:
        save_approval(
            session,
            Approval(
                project_id=stored.project_id,
                workflow_id=workflow_id,
                subject=f"coding-review:{new_id()}",
                status=ApprovalStatus.PENDING,
                comment="coding gate",
            ),
        )
        count = session.scalar(select(func.count()).select_from(ApprovalRow).where(ApprovalRow.workflow_id == workflow_id))
    assert count == 2


def test_conflicting_approval_rolls_back_and_returns_409() -> None:
    _postgres()
    settings = Settings(database_url=URL, coding_runtime="scripted", llm_api_key="postgres-test-key")
    ctx, _project, _requirement, workflow_id = _workflow(settings, status="researched")
    _run, artifact = _seed(ctx, _project, workflow_id, _report(["科学计数法只支持字面量，还是完整表达式？"]))
    service = PlanReviewService(
        PlanningWorkflowService(ctx, checkpointer=MemorySaver(), complete_fn=ScriptModel([_draft(), _draft()]))
    )
    service.planning.start(workflow_id, artifact.id)
    item = service.list_clarifications(workflow_id)[0]
    service.answer(workflow_id, item["clarification_id"], "完整数学表达式")
    revised = service.revise(workflow_id)
    with session_scope(ctx.sessions) as session:
        before = session.scalar(select(func.count()).select_from(ApprovalRow).where(ApprovalRow.workflow_id == workflow_id))
    with pytest.raises(WorkflowConflict):
        service.decide(workflow_id, "approved", "", revised["plan_artifact_id"], "wrong-hash")
    with session_scope(ctx.sessions) as session:
        after = session.scalar(select(func.count()).select_from(ApprovalRow).where(ApprovalRow.workflow_id == workflow_id))
    assert before == after == 0
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    winners: list[str] = []

    def _decide(decision: str, comment: str) -> None:
        try:
            barrier.wait(timeout=10)
            result = service.decide(workflow_id, decision, comment, revised["plan_artifact_id"], revised["plan_hash"])
            winners.append(result["status"])
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        threading.Thread(target=_decide, args=("approved", "确认")),
        threading.Thread(target=_decide, args=("rejected", "拒绝")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(20)
    assert winners == ["approved"] or (len(winners) == 1 and winners[0] in {"approved", "rejected"})
    assert any(isinstance(item, WorkflowConflict) for item in errors)
    with session_scope(ctx.sessions) as session:
        rows = session.scalars(select(ApprovalRow).where(ApprovalRow.workflow_id == workflow_id)).all()
    assert len(rows) == 1
    assert rows[0].status in {"approved", "rejected"}
