import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"
ROOT = Path(__file__).resolve().parents[1]
STATEMENT = "为计算器增加支持科学计数法的表达式计算功能，要求支持合理的运算优先级、括号、浮点数，并补充完整的单元测试。"


def _engine():
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError:
        pytest.skip("postgres is not reachable")
    return engine


def _require_live() -> None:
    image = subprocess.run(
        ["docker", "image", "inspect", "ghcr.io/openhands/agent-server:1.63.1-python"],
        capture_output=True,
    )
    if image.returncode != 0:
        pytest.skip("agent-server image is not present")
    env_file = (ROOT / ".env").read_text(encoding="utf-8") if (ROOT / ".env").exists() else ""
    if not os.environ.get("ASE_LLM_API_KEY") and "ASE_LLM_API_KEY=" not in env_file:
        pytest.skip("ASE_LLM_API_KEY is empty")


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start(env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "ase.main"],
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


def _short_root(name: str) -> Path:
    root = Path(r"E:\ase-ws") / name
    root.mkdir(parents=True, exist_ok=True)
    return root


def _base_env(tmp_path: Path, port: int) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "ASE_DATABASE_URL": URL,
            "ASE_CHECKPOINT_BACKEND": "postgres",
            "ASE_CODING_RUNTIME": "openhands",
            "ASE_API_PORT": str(port),
            "ASE_API_KEY": "live-key",
            "ASE_WORKSPACE_ROOT": str(tmp_path / "ws"),
            "ASE_OPENHANDS_MAX_ITERATIONS": "40",
            "ASE_MAX_REPAIR_ATTEMPTS": "1",
            "ASE_AGENT_TIMEOUT_SECONDS": "500",
            "OPENHANDS_SUPPRESS_BANNER": "1",
        }
    )
    return env


def _scalar(engine, sql: str, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def test_crash_after_tests_pass_rewrites_final_result_once(tmp_path, repo) -> None:
    _require_live()
    engine = _engine()
    main_before = subprocess.check_output(["git", "rev-parse", "main"], cwd=repo, text=True).strip()
    port = _port()
    env = _base_env(tmp_path, port)
    env["ASE_CRASH_POINT"] = "before_final_commit"
    first = _start(env)
    try:
        _ready(port, first)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False) as client:
            project = client.post("/api/v1/projects", json={"name": f"crash-{os.urandom(3).hex()}", "repo_path": str(repo)}).json()["data"]
            requirement = client.post(
                "/api/v1/requirements",
                json={"project_id": project["id"], "title": "crash", "statement": STATEMENT},
            ).json()["data"]
            created = client.post("/api/v1/workflows", json={"project_id": project["id"], "requirement_id": requirement["id"]}).json()["data"]
        workflow_id = created["workflow_id"]

        def approve() -> None:
            try:
                httpx.post(
                    f"http://127.0.0.1:{port}/api/v1/approvals/{created['approval_id']}/approve",
                    headers={"X-ASE-API-KEY": "live-key"},
                    json={"comment": "run until the final commit"},
                    timeout=500,
                    trust_env=False,
                )
            except Exception:
                return

        threading.Thread(target=approve).start()
        deadline = time.time() + 420
        while time.time() < deadline and first.poll() is None:
            time.sleep(1)
        assert first.poll() == 86, f"process ended with {first.poll()} before the final-commit crash"
        passed = _scalar(
            engine,
            "select tr.passed from test_results tr join executions e on e.id = tr.execution_id where e.workflow_id = :id order by tr.created_at desc limit 1",
            id=workflow_id,
        )
        assert passed is True
        execution_id = _scalar(engine, "select id from executions where workflow_id = :id order by created_at limit 1", id=workflow_id)
        conversation_id = _scalar(
            engine,
            "select conversation_id from executions where workflow_id = :id and conversation_id is not null limit 1",
            id=workflow_id,
        )
        tasks_before = _scalar(
            engine,
            "select count(*) from tasks where plan_id in (select id from development_plans where requirement_id = :id)",
            id=requirement["id"],
        )
        assert _scalar(engine, "select count(*) from evaluation_reports where workflow_id = :id", id=workflow_id) == 0
        assert _scalar(engine, "select count(*) from artifacts where workflow_id = :id", id=workflow_id) == 0
        env.pop("ASE_CRASH_POINT")
        second = _start(env)
        try:
            _ready(port, second)
            resumed = httpx.post(
                f"http://127.0.0.1:{port}/api/v1/workflows/{workflow_id}/continue",
                headers={"X-ASE-API-KEY": "live-key"},
                timeout=180,
                trust_env=False,
            )
            assert resumed.status_code == 200, resumed.text
            assert resumed.json()["data"]["status"] == "delivered"
        finally:
            _stop(second)
        assert _scalar(engine, "select id from executions where workflow_id = :id order by created_at limit 1", id=workflow_id) == execution_id
        assert _scalar(
            engine,
            "select count(distinct conversation_id) from execution_operations where workflow_id = :id and conversation_id is not null",
            id=workflow_id,
        ) == 1
        assert _scalar(
            engine,
            "select conversation_id from execution_operations where workflow_id = :id and conversation_id is not null limit 1",
            id=workflow_id,
        ) == conversation_id
        assert _scalar(
            engine,
            "select count(*) from tasks where plan_id in (select id from development_plans where requirement_id = :id)",
            id=requirement["id"],
        ) == tasks_before
        assert _scalar(engine, "select count(*) from evaluation_reports where workflow_id = :id", id=workflow_id) == 1
        assert _scalar(engine, "select count(*) from artifacts where workflow_id = :id", id=workflow_id) == 1
        assert subprocess.check_output(["git", "rev-parse", "main"], cwd=repo, text=True).strip() == main_before
        assert len(list((tmp_path / "ws").glob(f"{project['id']}/*"))) == 1
    finally:
        if first.poll() is None:
            _stop(first)


def test_agent_server_restart_keeps_the_conversation(tmp_path, repo) -> None:
    _require_live()
    engine = _engine()
    port = _port()
    workspace = _short_root(f"restart-{os.urandom(3).hex()}")
    env = _base_env(tmp_path, port)
    env["ASE_WORKSPACE_ROOT"] = str(workspace)
    env["ASE_OPENHANDS_MAX_ITERATIONS"] = "30"
    env["ASE_MAX_REPAIR_ATTEMPTS"] = "0"
    first = _start(env)
    try:
        _ready(port, first)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False) as client:
            project = client.post("/api/v1/projects", json={"name": f"restart-{os.urandom(3).hex()}", "repo_path": str(repo)}).json()["data"]
            requirement = client.post(
                "/api/v1/requirements",
                json={"project_id": project["id"], "title": "restart", "statement": STATEMENT},
            ).json()["data"]
            created = client.post("/api/v1/workflows", json={"project_id": project["id"], "requirement_id": requirement["id"]}).json()["data"]
        workflow_id = created["workflow_id"]
        tasks_before = _scalar(
            engine,
            "select count(*) from tasks where plan_id in (select id from development_plans where requirement_id = :id)",
            id=requirement["id"],
        )

        def approve() -> None:
            try:
                httpx.post(
                    f"http://127.0.0.1:{port}/api/v1/approvals/{created['approval_id']}/approve",
                    headers={"X-ASE-API-KEY": "live-key"},
                    json={"comment": "restart the server mid-run"},
                    timeout=400,
                    trust_env=False,
                )
            except Exception:
                return

        threading.Thread(target=approve).start()
        container_id = None
        conversation_id = None
        meta: list[Path] = []
        deadline = time.time() + 180
        while time.time() < deadline:
            container_id = _scalar(
                engine,
                "select container_id from executions where workflow_id = :id and container_id is not null limit 1",
                id=workflow_id,
            )
            conversation_id = _scalar(
                engine,
                "select conversation_id from executions where workflow_id = :id and conversation_id is not null limit 1",
                id=workflow_id,
            )
            if container_id and conversation_id:
                meta = list(workspace.glob(f"{project['id']}/*/workspace/conversations/{conversation_id.replace('-', '')}/meta.json"))
                if meta:
                    break
            time.sleep(0.5)
        assert container_id and conversation_id and meta, f"conversation files were not persisted: {list(workspace.rglob('*'))[:30]}"
        before = meta[0].read_text(encoding="utf-8")
        restarted = subprocess.run(["docker", "restart", container_id], capture_output=True, text=True)
        assert restarted.returncode == 0, restarted.stderr
        survived = time.time() + 20
        while time.time() < survived and not meta[0].exists():
            time.sleep(0.5)
        assert meta[0].exists()
        reloaded = meta[0].read_text(encoding="utf-8")
        assert conversation_id in reloaded or conversation_id.replace("-", "") in reloaded
        assert before
        deadline = time.time() + 300
        status = None
        while time.time() < deadline:
            status = _scalar(engine, "select status from workflows where id = :id", id=workflow_id)
            if status in {"delivered", "failed", "cancelled"}:
                break
            time.sleep(2)
        assert status == "delivered", status
        assert _scalar(
            engine,
            "select count(distinct conversation_id) from execution_operations where workflow_id = :id and conversation_id is not null",
            id=workflow_id,
        ) == 1
        assert _scalar(
            engine,
            "select conversation_id from execution_operations where workflow_id = :id and conversation_id is not null limit 1",
            id=workflow_id,
        ) == conversation_id
        assert _scalar(
            engine,
            "select count(*) from tasks where plan_id in (select id from development_plans where requirement_id = :id)",
            id=requirement["id"],
        ) == tasks_before
        assert _scalar(
            engine,
            "select count(*) from execution_events where workflow_id = :id and event_type = 'Tool Execution'",
            id=workflow_id,
        ) > 0
    finally:
        _stop(first)
        leftover = subprocess.run(["docker", "ps", "-q", "--filter", "label=ase.role=agent"], capture_output=True, text=True)
        for item in leftover.stdout.split():
            subprocess.run(["docker", "rm", "-f", item], capture_output=True)
        shutil.rmtree(workspace, ignore_errors=True)


def test_two_real_model_tasks_stay_isolated(tmp_path, repo) -> None:
    _require_live()
    engine = _engine()
    copies = []
    for name in ("left", "right"):
        target = tmp_path / name
        shutil.copytree(repo, target)
        copies.append(target)
    port = _port()
    workspace = _short_root(f"pair-{os.urandom(3).hex()}")
    env = _base_env(tmp_path, port)
    env["ASE_WORKSPACE_ROOT"] = str(workspace)
    server = _start(env)
    results: list[dict] = []
    try:
        _ready(port, server)

        def run(copy: Path, name: str) -> None:
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False) as client:
                    project = client.post("/api/v1/projects", json={"name": f"{name}-{os.urandom(3).hex()}", "repo_path": str(copy)}).json()["data"]
                    requirement = client.post(
                        "/api/v1/requirements",
                        json={"project_id": project["id"], "title": name, "statement": STATEMENT},
                    ).json()["data"]
                    created = client.post(
                        "/api/v1/workflows",
                        json={"project_id": project["id"], "requirement_id": requirement["id"]},
                    ).json()["data"]
                    approved = client.post(
                        f"/api/v1/approvals/{created['approval_id']}/approve",
                        headers={"X-ASE-API-KEY": "live-key"},
                        json={"comment": name},
                        timeout=600,
                    )
                results.append({"name": name, "status_code": approved.status_code, "body": approved.json(), "project_id": project["id"]})
            except Exception as exc:
                results.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})

        threads = [threading.Thread(target=run, args=(copy, copy.name)) for copy in copies]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        _stop(server)
        leftover = subprocess.run(["docker", "ps", "-q", "--filter", "label=ase.role=agent"], capture_output=True, text=True)
        for container_id in leftover.stdout.split():
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True)
    assert len(results) == 2, results
    assert all(item.get("status_code") == 200 for item in results), results
    statuses = [item["body"]["data"]["status"] for item in results]
    assert statuses == ["delivered", "delivered"], results
    workflow_ids = [item["body"]["data"]["workflow_id"] for item in results]
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "select workflow_id, conversation_id from execution_operations "
                "where workflow_id in (:left, :right) and conversation_id is not null"
            ),
            {"left": workflow_ids[0], "right": workflow_ids[1]},
        ).all()
    by_workflow: dict[str, set[str]] = {workflow_id: set() for workflow_id in workflow_ids}
    for workflow_id, conversation_id in rows:
        by_workflow[workflow_id].add(conversation_id)
    assert all(by_workflow.values())
    assert set().union(*by_workflow.values()) and by_workflow[workflow_ids[0]].isdisjoint(by_workflow[workflow_ids[1]])
    paths = [next(workspace.glob(f"{item['project_id']}/*")) for item in results]
    assert paths[0] != paths[1]
    with engine.connect() as connection:
        usage = connection.execute(
            text(
                "select workflow_id, model, prompt_tokens, completion_tokens from model_usages "
                "where workflow_id in (:left, :right)"
            ),
            {"left": workflow_ids[0], "right": workflow_ids[1]},
        ).all()
    seen = {row[0] for row in usage}
    assert seen == set(workflow_ids), usage
    assert all(row[2] is not None and row[3] is not None for row in usage), usage
    shutil.rmtree(workspace, ignore_errors=True)
