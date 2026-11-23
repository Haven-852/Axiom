"""One real DeepSeek plan revision of the Phase 2.2 calculator plan. Approval must fail."""

import hashlib
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError

from ase.app import _checkpointer, build_context
from ase.config import Settings
from ase.db.orm import DevelopmentPlanRow, ExecutionRow, ModelUsageRow, TaskRow
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.planning.review_service import PlanReviewService
from ase.planning.schemas import Plan
from ase.planning.service import PlanningWorkflowService
from ase.store import get_artifact, get_workflow, list_tasks_for_plan
import json

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"
WORKFLOW_ID = "1dd7c84d-36d1-4dcb-a66e-1004e0a1985f"
PLAN_ARTIFACT_ID = "b5b35a61-01a0-4971-a6b3-3e0c8587bcbc"
RESEARCH_ARTIFACT_ID = "450e2050-cd34-4dcc-8766-fec43e63fc82"
CALCULATOR = Path(__file__).resolve().parents[1] / "ase-poc-demo" / "calculator"
ANSWERS = (
    ("语法范围", "完整数学表达式"),
    ("大写 E", "支持大写 E、正负指数、空白和多个数值"),
    ("非法输入", "非法输入是抛出 ValueError、返回错误值"),
    ("NotImplementedError", "现有 NotImplementedError 测试应删除"),
    ("float", "使用 float"),
    ("语法与字面量", "完整数学表达式"),
)
OPEN_QUESTION = "输出格式"


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


def test_real_revision_keeps_unanswered_blocker() -> None:
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
    service = PlanReviewService(PlanningWorkflowService(ctx, checkpointer=_checkpointer(settings)))
    versions = service.list_versions(WORKFLOW_ID)
    assert versions[0]["plan_artifact_id"] == PLAN_ARTIFACT_ID
    assert versions[0]["source_research_artifact_id"] == RESEARCH_ARTIFACT_ID
    assert versions[0]["plan_version"] == 1
    with session_scope(ctx.sessions) as session:
        original = get_artifact(session, PLAN_ARTIFACT_ID).body
        executions_before = session.scalar(select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == WORKFLOW_ID))
        plans_before = session.scalar(select(func.count()).select_from(DevelopmentPlanRow))
        usage_before = session.scalar(
            select(func.count()).select_from(ModelUsageRow).where(ModelUsageRow.workflow_id == WORKFLOW_ID, ModelUsageRow.agent_name == "planning")
        )
        old_tasks = list_tasks_for_plan(session, versions[0]["plan_id"])
    old_status = {item.id: item.status.value for item in old_tasks}
    items = service.list_clarifications(WORKFLOW_ID)
    assert len(items) == 7
    matched = set()
    for item in items:
        if OPEN_QUESTION in item["question"]:
            assert item["state"] == "unresolved"
            continue
        answer = next((text for needle, text in ANSWERS if needle in item["question"]), None)
        assert answer is not None, item["question"]
        matched.add(item["question"])
        service.answer(WORKFLOW_ID, item["clarification_id"], answer)
    assert len(matched) == 6
    viewed = service.list_clarifications(WORKFLOW_ID)
    assert sum(item["state"] == "answered" for item in viewed) == 6
    assert any(OPEN_QUESTION in item["question"] and item["state"] == "unresolved" and item["blocking"] for item in viewed)
    with session_scope(ctx.sessions) as session:
        assert get_artifact(session, PLAN_ARTIFACT_ID).body == original
        assert {item.id: item.status.value for item in list_tasks_for_plan(session, versions[0]["plan_id"])} == old_status
    already = any(item["origin"] == "planning" and item["plan_version"] > 1 for item in versions)
    revised = service.revise(WORKFLOW_ID)
    assert revised["plan_version"] >= 2
    assert revised["parent_plan_artifact_id"] == PLAN_ARTIFACT_ID or revised["plan_version"] > 2
    assert revised["source_research_artifact_id"] == RESEARCH_ARTIFACT_ID
    with session_scope(ctx.sessions) as session:
        assert get_artifact(session, PLAN_ARTIFACT_ID).body == original
        envelope = json.loads(get_artifact(session, revised["plan_artifact_id"]).body)
        plan = Plan.model_validate(envelope["content"])
        usage = session.scalar(select(ModelUsageRow).where(ModelUsageRow.agent_run_id == revised["planning_agent_run_id"]))
        usage_after = session.scalar(
            select(func.count()).select_from(ModelUsageRow).where(ModelUsageRow.workflow_id == WORKFLOW_ID, ModelUsageRow.agent_name == "planning")
        )
        plans_after = session.scalar(select(func.count()).select_from(DevelopmentPlanRow))
        current_tasks = list_tasks_for_plan(session, plan.plan_id)
        executions_after = session.scalar(select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == WORKFLOW_ID))
        conversations = session.scalar(
            select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == WORKFLOW_ID, ExecutionRow.conversation_id.is_not(None))
        )
        coding_tasks = session.scalar(select(func.count()).select_from(TaskRow).where(TaskRow.agent_id == "coding", TaskRow.plan_id == plan.plan_id))
        status = get_workflow(session, WORKFLOW_ID).status
    assert plan.validation_result.valid is True
    assert envelope["research_artifact_id"] == RESEARCH_ARTIFACT_ID
    answers = {item.question: item.answer for item in plan.clarification_items}
    assert answers["非法输入应抛出 ValueError、返回错误值，还是其他行为？"] == "非法输入是抛出 ValueError、返回错误值"
    assert answers["是否需要保留 float 精度，还是要求高精度十进制计算？"] == "使用 float"
    open_item = next(item for item in plan.clarification_items if OPEN_QUESTION in item.question)
    assert open_item.status.value == "unresolved"
    assert open_item.blocking is True
    assert any(item.status.value == "blocked" and item.agent_id == "implementation" for item in current_tasks)
    assert usage is not None
    assert usage.provider == "deepseek"
    assert usage.model == "deepseek-flash"
    assert (usage.prompt_tokens or 0) > 0
    assert usage.estimated_cost is None
    if not already:
        assert usage_after == usage_before + 1
        assert plans_after == plans_before + 1
    with pytest.raises(WorkflowConflict) as blocked:
        service.decide(WORKFLOW_ID, "approved", "", revised["plan_artifact_id"], revised["plan_hash"])
    assert open_item.clarification_id in str(blocked.value)
    repeated = service.revise(WORKFLOW_ID)
    assert repeated["plan_artifact_id"] == revised["plan_artifact_id"]
    with session_scope(ctx.sessions) as session:
        assert session.scalar(
            select(func.count()).select_from(ModelUsageRow).where(ModelUsageRow.workflow_id == WORKFLOW_ID, ModelUsageRow.agent_name == "planning")
        ) == usage_after
        assert session.scalar(select(func.count()).select_from(DevelopmentPlanRow)) == plans_after
    restarted = build_context(settings)
    restored = PlanReviewService(PlanningWorkflowService(restarted, checkpointer=_checkpointer(settings)))
    restored_versions = restored.list_versions(WORKFLOW_ID)
    assert restored_versions[-1]["plan_artifact_id"] == revised["plan_artifact_id"]
    assert restored.history(WORKFLOW_ID, items[0]["clarification_id"])
    assert executions_after == executions_before
    assert conversations == 0
    assert coding_tasks == 0
    assert status == "planned"
    assert _hashes(CALCULATOR) == before
    git_after = subprocess.run(
        ["git", "status", "--porcelain", "--", "ase-poc-demo/calculator"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert git_after == git_before
    assert _docker_agents() == containers_before
