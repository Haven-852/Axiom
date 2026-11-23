import json
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


def test_real_openhands_calculator(tmp_path: Path) -> None:
    image = "ghcr.io/openhands/agent-server:1.63.1-python"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        pytest.skip("agent-server image is not present")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'ase.db'}",
        workspace_root=tmp_path / "workspaces",
        coding_runtime="openhands",
        ase_source_root=tmp_path / "ase-source",
        agent_timeout_seconds=900,
        openhands_max_iterations=40,
        max_repair_attempts=1,
    )
    key = settings.llm_api_key.get_secret_value()
    if not key:
        pytest.skip("ASE_LLM_API_KEY is empty")
    (tmp_path / "ase-source").mkdir()
    source = Path(__file__).resolve().parents[1] / "ase-poc-demo" / "calculator"
    repo = tmp_path / "calculator"
    shutil.copytree(source, repo)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=ase@example.com", "-c", "user.name=ASE", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    ctx = build_context(settings)
    project = Project(name="real-calculator", repo_path=str(repo), base_ref="HEAD")
    statement = "为计算器增加支持科学计数法的表达式计算功能，要求支持合理的运算优先级、括号、浮点数，并补充完整的单元测试。"
    requirement = Requirement(project_id=project.id, title="科学计数法", statement=statement)
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
    service = WorkflowService(ctx)
    paused = service.start(project.id, requirement.id, statement)
    assert paused["status"] == "waiting_approval"
    finished = service.resume(paused["workflow_id"], "approved", "approved for the isolated worktree")
    report_dir = Path(__file__).resolve().parents[1] / "docs" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    safe = json.loads(json.dumps(finished).replace(key, "[redacted]"))
    (report_dir / "real-poc-log.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    diff = (finished.get("values") or {}).get("diff") or ""
    (report_dir / "real-poc.diff").write_text(diff, encoding="utf-8")
    assert key not in json.dumps(safe)
    assert finished["status"] == "delivered", finished["status"]
    kinds = [event["event_type"] for event in finished["events"]]
    assert "Workspace Created" in kinds
    assert "Test Completed" in kinds
    workspace = Path(finished["values"]["workspace"]["path"])
    source = (workspace / "calculator.py").read_text(encoding="utf-8")
    tests = (workspace / "test_calculator.py").read_text(encoding="utf-8")
    assert "NotImplementedError" not in source
    assert "evaluate" in source
    assert "NotImplementedError" not in tests
    main = subprocess.check_output(["git", "rev-parse", "main"], cwd=repo, text=True).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    assert head == main
