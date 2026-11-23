"""The unapproved calculator plan must not start a coding execution."""

import hashlib
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import OperationalError

from ase.agents.coding_agent import CodingAgent, coding_definition
from ase.agents.registry import AgentRegistry
from ase.agents.runner import AgentRunner
from ase.app import build_context
from ase.config import Settings
from ase.db.orm import ExecutionRow
from ase.db.session import session_scope
from ase.execution.coding_service import CodingTaskService
from ase.execution.eligibility import EligibilityError
from ase.store import get_workflow

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"
WORKFLOW_ID = "1dd7c84d-36d1-4dcb-a66e-1004e0a1985f"
READY_TASK = "8266a91d-8694-56d8-b615-7784e6436e09"
BLOCKED_TASK = "1d97ddce-d9dc-55b0-bace-69ee82c9f19e"
OPEN_QUESTIONS = ("c70c3a8e-4fcb-5fb1-8ed7-9efcd245a679", "c7d1346e-5a7c-556e-9e9a-da556fa2ca3c")
CALCULATOR = Path(__file__).resolve().parents[1] / "ase-poc-demo" / "calculator"


def _hashes(root: Path) -> dict[str, str]:
    found = {}
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def test_calculator_plan_is_not_executable() -> None:
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError as exc:
        pytest.skip(f"postgres is not reachable: {exc.__class__.__name__}")
    settings = Settings(database_url=URL, coding_runtime="scripted", llm_api_key="calculator-gate")
    before = _hashes(CALCULATOR)
    git_before = subprocess.run(
        ["git", "status", "--porcelain", "--", "ase-poc-demo/calculator"],
        cwd=CALCULATOR.parents[1],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    containers_before = subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=ase.role=agent"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout
    ctx = build_context(settings)
    registry = AgentRegistry()
    registry.register(coding_definition(), lambda: CodingAgent(ctx.coding, ctx.sessions))
    service = CodingTaskService(ctx, AgentRunner(registry, ctx.sessions, settings))
    with session_scope(ctx.sessions) as session:
        executions_before = session.scalar(select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == WORKFLOW_ID))
    for task_id in (READY_TASK, BLOCKED_TASK):
        with pytest.raises(EligibilityError) as rejected:
            service.eligibility(WORKFLOW_ID, task_id)
        assert rejected.value.code == "plan_not_approved"
    with pytest.raises(EligibilityError) as blocked:
        service.eligibility(WORKFLOW_ID, BLOCKED_TASK)
    assert OPEN_QUESTIONS[0] in str(blocked.value)
    with session_scope(ctx.sessions) as session:
        executions_after = session.scalar(select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == WORKFLOW_ID))
        status = get_workflow(session, WORKFLOW_ID).status
    assert executions_after == executions_before
    assert status == "planned"
    assert _hashes(CALCULATOR) == before
    git_after = subprocess.run(
        ["git", "status", "--porcelain", "--", "ase-poc-demo/calculator"],
        cwd=CALCULATOR.parents[1],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert git_after == git_before
    containers_after = subprocess.run(
        ["docker", "ps", "-q", "--filter", "label=ase.role=agent"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout
    assert containers_after == containers_before
    assert OPEN_QUESTIONS
