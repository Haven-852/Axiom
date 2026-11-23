"""One real DeepSeek research run. This is Level 1 evidence, not a mocked model."""

import hashlib
import json
import os
import subprocess

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from ase.agents.registry import AgentRegistry
from ase.agents.research.agent import ResearchAgent, research_definition
from ase.agents.research.schemas import ResearchReport
from ase.agents.research.services import research_key, research_options
from ase.agents.runner import AgentRunner
from ase.app import _checkpointer, build_context
from ase.config import Settings
from ase.contracts.domain import Project, Requirement, new_id
from ase.db.orm import ExecutionRow, ModelUsageRow
from ase.db.session import session_scope
from ase.orchestration.research_graph import build_research_graph
from ase.orchestration.research_nodes import bind_research_runner, reset_research_runner
from ase.store import get_artifact, save_project, save_requirement, save_workflow
from sqlalchemy import func, select

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"


def _hashes(root) -> dict[str, str]:
    found = {}
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        found[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def test_real_deepseek_research_workflow(repo, tmp_path) -> None:
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError as exc:
        pytest.skip(f"postgres is not reachable: {exc.__class__.__name__}")
    settings = Settings(
        database_url=URL,
        workspace_root=tmp_path / "workspaces",
        coding_runtime="scripted",
        checkpoint_backend="postgres",
        ase_source_root=repo,
    )
    if not settings.llm_api_key.get_secret_value().strip():
        pytest.skip("ASE_LLM_API_KEY is empty")
    env = os.environ.copy()
    env["ASE_DATABASE_URL"] = URL
    upgraded = subprocess.run(
        [".venv/Scripts/python.exe", "-m", "alembic", "upgrade", "head"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    before = _hashes(repo)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True)
    assert status.stdout.strip() == ""
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    ctx = build_context(settings)
    project = Project(name=f"live-research-{os.urandom(3).hex()}", repo_path=str(repo), base_ref="HEAD")
    requirement = Requirement(project_id=project.id, title="research", statement="为计算器增加科学计数法求值，并说明现有 evaluate 的状态。")
    workflow_id = new_id()
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
        save_workflow(session, workflow_id, project.id, requirement.id, "running")
    options = research_options({})
    registry = AgentRegistry()
    registry.register(research_definition(), lambda: ResearchAgent(settings, ctx.sessions))
    runner = AgentRunner(registry, ctx.sessions, settings)
    saver = _checkpointer(settings)
    config = {"configurable": {"thread_id": workflow_id}}
    initial = {
        "workflow_id": workflow_id,
        "project_id": project.id,
        "requirement": requirement.statement,
        "research_key": research_key(workflow_id, requirement.statement, revision, "1", options),
        "agent_run_ids": [],
        "artifact_ids": [],
        **options,
    }
    token = bind_research_runner(runner)
    try:
        paused = build_research_graph(saver, interrupt_before=["research"])
        paused.invoke(initial, config)
        assert paused.get_state(config).next == ("research",)
        with engine.connect() as connection:
            checkpoints = connection.execute(
                text("select count(*) from checkpoints where thread_id = :id"),
                {"id": workflow_id},
            ).scalar()
        assert checkpoints >= 1
        final = build_research_graph(saver).invoke(None, config)
    finally:
        reset_research_runner(token)
    report = ResearchReport.model_validate(final["research_report"])
    assert report.schema_version == "1.0"
    assert "Python" in report.project_analysis.languages
    assert report.research_summary
    assert final["agent_run_ids"]
    assert final["artifact_ids"]
    with session_scope(ctx.sessions) as session:
        artifact = get_artifact(session, final["artifact_ids"][-1])
        usage_rows = session.scalars(select(ModelUsageRow).where(ModelUsageRow.workflow_id == workflow_id)).all()
        executions = session.scalar(select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == workflow_id))
    assert executions == 0
    body = json.loads(artifact.body)
    assert body["agent_run_id"] == final["agent_run_ids"][-1]
    assert body["revision"] == revision
    assert isinstance(body["files_read"], int) and body["files_read"] > 0
    secret = settings.llm_api_key.get_secret_value()
    assert secret not in artifact.body
    assert usage_rows
    prompt = sum(row.prompt_tokens or 0 for row in usage_rows)
    completion = sum(row.completion_tokens or 0 for row in usage_rows)
    assert any(isinstance(row.prompt_tokens, int) or isinstance(row.completion_tokens, int) for row in usage_rows)
    assert _hashes(repo) == before
    after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True)
    assert after.stdout.strip() == ""
    print(
        "RESEARCH_LIVE "
        f"workflow={workflow_id} agent_run={body['agent_run_id']} artifact={artifact.id} "
        f"revision={revision} files_read={body['files_read']} search_results={body['search_results']} "
        f"prompt_tokens={prompt} completion_tokens={completion} "
        f"estimated_cost={usage_rows[-1].estimated_cost} model={usage_rows[-1].model} provider={usage_rows[-1].provider}"
    )
