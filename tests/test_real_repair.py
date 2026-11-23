import shutil
import subprocess
from pathlib import Path

import pytest

from ase.app import build_context
from ase.config import Settings
from ase.contracts.domain import Project, Requirement
from ase.db.session import session_scope
from ase.store import save_project, save_requirement
from ase.workflow.service import WorkflowService


def test_real_test_failure_enters_one_repair(tmp_path: Path) -> None:
    image = "ghcr.io/openhands/agent-server:1.63.1-python"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        pytest.skip("agent-server image is not present")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ase.db'}",
        workspace_root=tmp_path / "workspaces",
        coding_runtime="openhands",
        ase_source_root=tmp_path / "ase-source",
        agent_timeout_seconds=240,
        openhands_max_iterations=1,
        max_repair_attempts=1,
    )
    if not settings.llm_api_key.get_secret_value():
        pytest.skip("ASE_LLM_API_KEY is empty")
    (tmp_path / "ase-source").mkdir()
    source = Path(__file__).resolve().parents[1] / "ase-poc-demo" / "calculator"
    repo = tmp_path / "calculator"
    shutil.copytree(source, repo)
    (repo / "test_calculator.py").write_text(
        "from calculator import evaluate\n\n\ndef test_scientific() -> None:\n    assert evaluate('1.5e2') == 150\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=ase@example.com", "-c", "user.name=ASE", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    ctx = build_context(settings)
    project = Project(name="repair-calculator", repo_path=str(repo), base_ref="HEAD")
    requirement = Requirement(project_id=project.id, title="科学计数法", statement="实现 evaluate('1.5e2') == 150，不要使用 eval。")
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
    service = WorkflowService(ctx)
    paused = service.start(project.id, requirement.id, requirement.statement)
    finished = service.resume(paused["workflow_id"], "approved", "repair check")
    kinds = [event["event_type"] for event in finished["events"]]
    assert finished["values"]["repair_count"] <= 1
    assert kinds.count("Repair Completed") <= 1
    assert "Diagnosis Completed" in kinds
    assert "Repair Completed" in kinds
    assert "Test Completed" in kinds
    main = subprocess.check_output(["git", "rev-parse", "main"], cwd=repo, text=True).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    assert head == main
