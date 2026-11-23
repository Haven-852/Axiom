import os
import socket
import subprocess
import threading
import time

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _engine():
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError as exc:
        pytest.skip(exc.__class__.__name__)
    return engine


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start(env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [os.path.join(ROOT, ".venv", "Scripts", "python.exe"), "-m", "ase.main"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_ready(port: int, proc: subprocess.Popen) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"ASE exited with {proc.returncode}")
        try:
            httpx.get(f"http://127.0.0.1:{port}/openapi.json", timeout=1, trust_env=False)
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("ASE did not start")


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=15)


def test_killed_process_resumes_the_same_approval(tmp_path, repo) -> None:
    engine = _engine()
    port = _port()
    env = os.environ.copy()
    env.update(
        {
            "ASE_DATABASE_URL": URL,
            "ASE_CHECKPOINT_BACKEND": "postgres",
            "ASE_CODING_RUNTIME": "scripted",
            "ASE_API_PORT": str(port),
            "ASE_API_KEY": "recover-key",
            "ASE_WORKSPACE_ROOT": str(tmp_path / "ws"),
            "ASE_LLM_API_KEY": "",
            "OPENHANDS_SUPPRESS_BANNER": "1",
        }
    )
    first = _start(env)
    try:
        _wait_ready(port, first)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False) as client:
            created_project = client.post("/api/v1/projects", json={"name": f"kill-{os.urandom(3).hex()}", "repo_path": str(repo)})
            assert created_project.status_code == 200, created_project.text
            project = created_project.json()["data"]
            requirement = client.post(
                "/api/v1/requirements",
                json={"project_id": project["id"], "title": "persist", "statement": "增加科学计数法"},
            ).json()["data"]
            created = client.post("/api/v1/workflows", json={"project_id": project["id"], "requirement_id": requirement["id"]}).json()["data"]
        workflow_id = created["workflow_id"]
        assert created["status"] == "waiting_approval"
        with engine.connect() as connection:
            tasks_before = connection.execute(
                text("select count(*) from tasks where plan_id in (select id from development_plans where requirement_id = :id)"),
                {"id": requirement["id"]},
            ).scalar()
        _stop(first)
        second = _start(env)
        try:
            _wait_ready(port, second)
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60, trust_env=False) as client:
                waiting = client.get(f"/api/v1/workflows/{workflow_id}").json()["data"]
                assert waiting["status"] == "waiting_approval"
                resumed = client.post(
                    f"/api/v1/approvals/{waiting['approval_id']}/approve",
                    headers={"X-ASE-API-KEY": "recover-key"},
                    json={"comment": "resume after process kill"},
                )
            assert resumed.status_code == 200
            with engine.connect() as connection:
                tasks_after = connection.execute(
                    text("select count(*) from tasks where plan_id in (select id from development_plans where requirement_id = :id)"),
                    {"id": requirement["id"]},
                ).scalar()
                approvals = connection.execute(
                    text("select count(*) from approvals where workflow_id = :id"),
                    {"id": workflow_id},
                ).scalar()
            assert tasks_after == tasks_before
            assert approvals == 1
            worktrees = list((tmp_path / "ws").glob(f"{project['id']}/*"))
            assert len(worktrees) == 1
        finally:
            _stop(second)
    finally:
        if first.poll() is None:
            _stop(first)
