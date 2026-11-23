import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from ase.agents.llm_client import complete
from ase.app import build_context, create_app
from ase.config import Settings
from ase.contracts.domain import ExecutionEvent, EventSource
from ase.db.session import session_scope
from ase.infra.docker_sandbox import DockerSandbox
from ase.infra.agent_container import AgentContainerManager
from ase.store import save_event
from tests.doubles import GraphSandbox

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"


def _postgres():
    engine = create_engine(URL, pool_pre_ping=True, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError as exc:
        pytest.skip(exc.__class__.__name__)
    return engine


def test_llm_request_times_out(tmp_path: Path) -> None:
    ready = threading.Event()

    def serve() -> None:
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        serve.port = server.getsockname()[1]
        ready.set()
        connection, _address = server.accept()
        time.sleep(5)
        connection.close()
        server.close()

    thread = threading.Thread(target=serve)
    thread.start()
    ready.wait(2)
    settings = Settings(
        llm_api_base=f"http://127.0.0.1:{serve.port}",
        llm_api_key="timeout-token",
        llm_timeout=1,
        workspace_root=tmp_path,
    )
    with pytest.raises(RuntimeError) as caught:
        complete(settings, "system", "user")
    assert "timeout" in str(caught.value).lower()
    assert "timeout-token" not in str(caught.value)
    thread.join(6)


def test_pytest_command_times_out_inside_container(tmp_path: Path) -> None:
    image = "ghcr.io/openhands/agent-server:1.63.1-python"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        pytest.skip("agent image is not present")
    source = tmp_path / "source"
    source.mkdir()
    workspace = tmp_path / "ws" / "task"
    workspace.mkdir(parents=True)
    settings = Settings(
        workspace_root=tmp_path / "ws",
        ase_source_root=source,
        agent_server_image=image,
        coding_runtime="scripted",
        llm_api_key="",
    )
    manager = AgentContainerManager(settings)
    handle = manager.start(workspace)
    try:
        code, output, elapsed = manager.execute(
            handle.container_id,
            ["python", "-c", "import time; time.sleep(30)"],
            timeout=2,
        )
    finally:
        manager.stop_all()
    assert code == 124
    assert "timed out" in output
    assert elapsed < 15


def test_missing_container_does_not_run_host_pytest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    workspace = tmp_path / "ws" / "task"
    workspace.mkdir(parents=True)
    settings = Settings(workspace_root=tmp_path / "ws", ase_source_root=source, coding_runtime="scripted", llm_api_key="")
    result = DockerSandbox(settings, AgentContainerManager(settings)).run_pytest(workspace, "exec-1", None)
    assert result.exit_code == 127
    assert result.passed is False
    assert "Host execution is disabled" in result.output


def test_postgres_pause_and_event_rollback(tmp_path, repo) -> None:
    engine = _postgres()
    import os

    env = os.environ.copy()
    env["ASE_DATABASE_URL"] = URL
    upgraded = subprocess.run(
        [".venv/Scripts/python.exe", "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    settings = Settings(
        database_url=URL,
        workspace_root=tmp_path / "ws",
        api_key="fault-key",
        coding_runtime="scripted",
        ase_source_root=repo,
        checkpoint_backend="postgres",
    )
    ctx = build_context(settings, sandbox=GraphSandbox())
    client = TestClient(create_app(settings, ctx))
    project = client.post("/api/v1/projects", json={"name": f"fault-{time.time_ns()}", "repo_path": str(repo)}).json()["data"]
    requirement = client.post(
        "/api/v1/requirements",
        json={"project_id": project["id"], "title": "fault", "statement": "增加科学计数法"},
    ).json()["data"]
    created = client.post("/api/v1/workflows", json={"project_id": project["id"], "requirement_id": requirement["id"]})
    assert created.status_code == 200
    body = created.json()["data"]
    duplicate = client.post("/api/v1/workflows", json={"project_id": project["id"], "requirement_id": requirement["id"]})
    assert duplicate.status_code == 409
    cancelled = client.post(
        f"/api/v1/workflows/{body['workflow_id']}/cancel",
        headers={"X-ASE-API-KEY": "fault-key"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "cancelled"
    denied = client.post(
        f"/api/v1/approvals/{body['approval_id']}/approve",
        headers={"X-ASE-API-KEY": "fault-key"},
        json={"comment": "too late"},
    )
    assert denied.status_code == 409
    with engine.connect() as connection:
        before = connection.execute(text("select count(*) from execution_events")).scalar()
    with pytest.raises(RuntimeError):
        with session_scope(ctx.sessions) as session:
            save_event(
                session,
                ExecutionEvent(
                    project_id=project["id"],
                    workflow_id=body["workflow_id"],
                    event_type="Should Roll Back",
                    source=EventSource.ASE,
                    payload={},
                ),
            )
            raise RuntimeError("event write failed")
    with engine.connect() as connection:
        after = connection.execute(text("select count(*) from execution_events where event_type = 'Should Roll Back'")).scalar()
    assert after == 0
    assert before >= 0
    paused = subprocess.run(["docker", "pause", "axiom-postgres-1"], capture_output=True, text=True)
    assert paused.returncode == 0, paused.stderr
    try:
        blocked = create_engine(URL, pool_pre_ping=True, connect_args={"connect_timeout": 2})
        with pytest.raises(Exception):
            with blocked.connect() as connection:
                connection.execute(text("select 1"))
    finally:
        subprocess.run(["docker", "unpause", "axiom-postgres-1"], check=False)
    with engine.connect() as connection:
        connection.execute(text("select 1"))


def test_missing_agent_image_is_refused(tmp_path: Path) -> None:
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker is not available")
    source = tmp_path / "source"
    source.mkdir()
    workspace = tmp_path / "ws" / "task"
    workspace.mkdir(parents=True)
    settings = Settings(
        workspace_root=tmp_path / "ws",
        ase_source_root=source,
        agent_server_image="ase-missing-agent-server:none",
        coding_runtime="scripted",
        llm_api_key="",
    )
    with pytest.raises(RuntimeError) as caught:
        AgentContainerManager(settings).start(workspace)
    assert "missing" in str(caught.value).lower()
    running = subprocess.run(
        ["docker", "ps", "-q", "--filter", "ancestor=ase-missing-agent-server:none"],
        capture_output=True,
        text=True,
    )
    assert running.stdout.strip() == ""
