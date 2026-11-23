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


def _ready(port: int, proc: subprocess.Popen) -> None:
    deadline = time.time() + 40
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


def test_killed_openhands_run_reattaches_same_conversation(tmp_path, repo) -> None:
    image = subprocess.run(["docker", "image", "inspect", "ghcr.io/openhands/agent-server:1.63.1-python"], capture_output=True)
    if image.returncode != 0:
        pytest.skip("agent-server image is not present")
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError:
        pytest.skip("postgres is not reachable")
    if not os.environ.get("ASE_LLM_API_KEY") and "ASE_LLM_API_KEY=" not in open(os.path.join(ROOT, ".env"), encoding="utf-8").read():
        pytest.skip("ASE_LLM_API_KEY is empty")
    port = _port()
    env = os.environ.copy()
    env.update(
        {
            "ASE_DATABASE_URL": URL,
            "ASE_CHECKPOINT_BACKEND": "postgres",
            "ASE_CODING_RUNTIME": "openhands",
            "ASE_API_PORT": str(port),
            "ASE_API_KEY": "recover-key",
            "ASE_WORKSPACE_ROOT": str(tmp_path / "ws"),
            "ASE_OPENHANDS_MAX_ITERATIONS": "8",
            "ASE_MAX_REPAIR_ATTEMPTS": "0",
            "ASE_AGENT_TIMEOUT_SECONDS": "240",
            "OPENHANDS_SUPPRESS_BANNER": "1",
        }
    )
    first = _start(env)
    workflow_id = ""
    try:
        _ready(port, first)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False) as client:
            project = client.post("/api/v1/projects", json={"name": f"oh-{os.urandom(3).hex()}", "repo_path": str(repo)}).json()["data"]
            requirement = client.post(
                "/api/v1/requirements",
                json={"project_id": project["id"], "title": "resume", "statement": "实现 evaluate('1.5e2') == 150，不要使用 eval。"},
            ).json()["data"]
            created = client.post("/api/v1/workflows", json={"project_id": project["id"], "requirement_id": requirement["id"]}).json()["data"]
        workflow_id = created["workflow_id"]
        approval_id = created["approval_id"]

        def approve() -> None:
            try:
                httpx.post(
                    f"http://127.0.0.1:{port}/api/v1/approvals/{approval_id}/approve",
                    headers={"X-ASE-API-KEY": "recover-key"},
                    json={"comment": "start the real agent"},
                    timeout=240,
                    trust_env=False,
                )
            except Exception:
                return

        worker = threading.Thread(target=approve)
        worker.start()
        conversation_id = None
        deadline = time.time() + 180
        while time.time() < deadline:
            with engine.connect() as connection:
                conversation_id = connection.execute(
                    text("select conversation_id from executions where workflow_id = :id and conversation_id is not null"),
                    {"id": workflow_id},
                ).scalar()
            if conversation_id:
                break
            time.sleep(0.5)
        assert conversation_id, "execution did not record a conversation id"
        running = subprocess.run(["docker", "ps", "-q", "--filter", "label=ase.role=agent"], capture_output=True, text=True)
        assert running.stdout.strip(), "agent container was not running when ASE was killed"
        _stop(first)
        worker.join(5)
        still = subprocess.run(["docker", "ps", "-q", "--filter", "label=ase.role=agent"], capture_output=True, text=True)
        assert still.stdout.strip(), "killing ASE removed the agent container"
        second = _start(env)
        try:
            _ready(port, second)
            resumed = httpx.post(
                f"http://127.0.0.1:{port}/api/v1/workflows/{workflow_id}/continue",
                headers={"X-ASE-API-KEY": "recover-key"},
                timeout=300,
                trust_env=False,
            )
            assert resumed.status_code == 200, resumed.text
            body = resumed.json()["data"]
            assert body["status"] in {"delivered", "failed", "testing", "evaluating"}
            with engine.connect() as connection:
                ids = connection.execute(
                    text("select distinct conversation_id from execution_operations where workflow_id = :id and conversation_id is not null"),
                    {"id": workflow_id},
                ).scalars().all()
            assert ids == [conversation_id]
        finally:
            _stop(second)
    finally:
        if first.poll() is None:
            _stop(first)
        subprocess.run(["docker", "ps", "-q", "--filter", "label=ase.role=agent"], capture_output=True, text=True)
        leftover = subprocess.run(["docker", "ps", "-q", "--filter", "label=ase.role=agent"], capture_output=True, text=True)
        for container_id in leftover.stdout.split():
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)
