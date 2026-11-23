"""One real DeepSeek planning run against the Phase 2.1 research artifact."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError

from ase.app import _checkpointer, build_context
from ase.config import Settings
from ase.db.orm import DevelopmentPlanRow, ExecutionRow, ModelUsageRow, TaskRow
from ase.db.session import session_scope
from ase.orchestration.planning_graph import build_planning_graph
from ase.orchestration.planning_nodes import bind_planning_runner, reset_planning_runner
from ase.planning.research_input import load_planning_input, planning_key
from ase.planning.schemas import Plan
from ase.planning.service import PlanningWorkflowService
from ase.store import get_agent_run_by_key, get_workflow

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"
WORKFLOW_ID = "1dd7c84d-36d1-4dcb-a66e-1004e0a1985f"
ARTIFACT_ID = "450e2050-cd34-4dcc-8766-fec43e63fc82"
CALCULATOR = Path(__file__).resolve().parents[1] / "ase-poc-demo" / "calculator"


def _hashes(root: Path) -> dict[str, str]:
    found = {}
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _docker_agents() -> str:
    result = subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=ase.role=agent"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return f"docker-unavailable:{result.returncode}"
    return result.stdout


def test_real_deepseek_planning_workflow() -> None:
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError as exc:
        pytest.skip(f"postgres is not reachable: {exc.__class__.__name__}")
    settings = Settings(database_url=URL, coding_runtime="scripted", checkpoint_backend="postgres")
    if not settings.llm_api_key.get_secret_value().strip():
        pytest.skip("ASE_LLM_API_KEY is empty")
    before = _hashes(CALCULATOR)
    repo_root = CALCULATOR.parents[1]
    git_before = subprocess.run(
        ["git", "status", "--porcelain", "--", "ase-poc-demo/calculator"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    containers_before = _docker_agents()
    ctx = build_context(settings)
    with session_scope(ctx.sessions) as session:
        loaded = load_planning_input(session, WORKFLOW_ID, ARTIFACT_ID)
    key = planning_key(loaded)
    thread_id = f"{WORKFLOW_ID}:planning"
    with session_scope(ctx.sessions) as session:
        workflow = get_workflow(session, WORKFLOW_ID)
        executions_before = session.scalar(select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == WORKFLOW_ID))
        usage_before = session.scalar(
            select(func.count()).select_from(ModelUsageRow).where(ModelUsageRow.workflow_id == WORKFLOW_ID, ModelUsageRow.agent_name == "planning")
        )
        conversations_before = session.scalar(
            select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == WORKFLOW_ID, ExecutionRow.conversation_id.is_not(None))
        )
        existing = get_agent_run_by_key(session, key)
    assert workflow is not None
    assert loaded.research_agent_run_id == "c36ae47a-eaf4-43d7-8c1d-d11af093eb9e"
    if existing is None or existing.status.value != "completed":
        with engine.begin() as connection:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                connection.execute(text(f"delete from {table} where thread_id = :id"), {"id": thread_id})
    saver = _checkpointer(settings)
    config = {"configurable": {"thread_id": thread_id}}
    service = PlanningWorkflowService(ctx, checkpointer=saver)
    token = bind_planning_runner(service.runner)
    try:
        paused = build_planning_graph(saver, interrupt_before=["planning"])
        paused.invoke(
            {
                "workflow_id": WORKFLOW_ID,
                "project_id": loaded.project_id,
                "research_artifact_id": ARTIFACT_ID,
                "planning_key": key,
                "agent_run_ids": [],
                "artifact_ids": [],
            },
            config,
        )
        assert paused.get_state(config).next == ("planning",)
        with engine.connect() as connection:
            checkpoints = connection.execute(text("select count(*) from checkpoints where thread_id = :id"), {"id": thread_id}).scalar()
        assert checkpoints >= 1
        final = build_planning_graph(saver).invoke(None, config)
    finally:
        reset_planning_runner(token)
    plan = Plan.model_validate(final["plan"])
    assert plan.validation_result.valid is True
    assert plan.validation_result.blocked_task_count >= 1
    assert plan.clarification_items
    assert all(item.status.value == "unresolved" for item in plan.clarification_items)
    rendered = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)
    for phrase in ("已确认完整表达式", "已确认只支持字面量", "用户要求完整表达式", "用户要求仅支持字面量", "用户已确认"):
        assert phrase not in rendered
    started = service.start(WORKFLOW_ID, ARTIFACT_ID)
    assert started["plan_id"] == plan.plan_id
    with session_scope(ctx.sessions) as session:
        workflow = get_workflow(session, WORKFLOW_ID)
        tasks = session.scalars(select(TaskRow).where(TaskRow.plan_id == plan.plan_id)).all()
        plans = session.scalar(select(func.count()).select_from(DevelopmentPlanRow).where(DevelopmentPlanRow.id == plan.plan_id))
        usage_rows = session.scalars(
            select(ModelUsageRow)
            .where(ModelUsageRow.workflow_id == WORKFLOW_ID, ModelUsageRow.agent_name == "planning")
            .order_by(ModelUsageRow.created_at)
        ).all()
        executions_after = session.scalar(select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == WORKFLOW_ID))
        conversations_after = session.scalar(
            select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == WORKFLOW_ID, ExecutionRow.conversation_id.is_not(None))
        )
    assert workflow.status == "planned"
    assert plans == 1
    assert len(tasks) == len(plan.tasks)
    assert executions_after == executions_before
    assert conversations_after == conversations_before
    if existing is None or existing.status.value != "completed":
        assert len(usage_rows) == (usage_before or 0) + 1
    else:
        assert len(usage_rows) == (usage_before or 0)
    usage = usage_rows[-1]
    assert _hashes(CALCULATOR) == before
    git_after = subprocess.run(
        ["git", "status", "--porcelain", "--", "ase-poc-demo/calculator"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert git_after == git_before
    containers_after = _docker_agents()
    assert containers_after == containers_before
    repeated = service.start(WORKFLOW_ID, ARTIFACT_ID)
    with session_scope(ctx.sessions) as session:
        usage_after_repeat = session.scalar(
            select(func.count()).select_from(ModelUsageRow).where(ModelUsageRow.workflow_id == WORKFLOW_ID, ModelUsageRow.agent_name == "planning")
        )
        tasks_after_repeat = session.scalar(select(func.count()).select_from(TaskRow).where(TaskRow.plan_id == plan.plan_id))
    assert repeated["plan_id"] == plan.plan_id
    assert usage_after_repeat == len(usage_rows)
    assert tasks_after_repeat == len(tasks)
    secret = settings.llm_api_key.get_secret_value()
    assert secret not in rendered
    print(
        "PLANNING_LIVE "
        f"workflow={WORKFLOW_ID} research_artifact={ARTIFACT_ID} research_run={loaded.research_agent_run_id} "
        f"planning_run={final['agent_run_ids'][-1]} plan_artifact={final['artifact_ids'][-1]} plan_id={plan.plan_id} "
        f"task_ids={','.join(plan.execution_order)} blocked={','.join(started['blocked_task_ids'])} "
        f"tasks={plan.validation_result.task_count} blocked_count={plan.validation_result.blocked_task_count} "
        f"executable={plan.validation_result.executable_task_count} clarifications={len(plan.clarification_items)} "
        f"dag_valid={plan.validation_result.valid} revision={loaded.repository_revision} "
        f"prompt_tokens={usage.prompt_tokens} completion_tokens={usage.completion_tokens} total_tokens={usage.total_tokens} "
        f"latency={usage.latency_seconds} estimated_cost={usage.estimated_cost} provider={usage.provider} model={usage.model} "
        f"executions={executions_after} conversations={conversations_after} containers={containers_after.count(chr(10))}"
    )
