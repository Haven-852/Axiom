import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from ase.app import build_context, create_app
from ase.config import Settings
from tests.doubles import GraphSandbox

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"


def _postgres():
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError as exc:
        pytest.skip(f"postgres is not reachable: {exc.__class__.__name__}")
    return engine


def test_postgres_checkpoint_conflict_and_restart(repo, tmp_path) -> None:
    engine = _postgres()
    env = os.environ.copy()
    env["ASE_DATABASE_URL"] = URL
    import subprocess

    upgraded = subprocess.run(
        [".venv/Scripts/python.exe", "-m", "alembic", "upgrade", "head"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    settings = Settings(
        database_url=URL,
        workspace_root=tmp_path / "workspaces",
        api_key="pg-key",
        coding_runtime="scripted",
        ase_source_root=repo,
        checkpoint_backend="postgres",
    )
    ctx = build_context(settings, sandbox=GraphSandbox())
    client = TestClient(create_app(settings, ctx))
    name = f"pg12-{os.urandom(4).hex()}"
    project = client.post(
        "/api/v1/projects",
        json={"name": name, "repo_path": str(repo), "base_ref": "HEAD"},
    ).json()["data"]
    requirement = client.post(
        "/api/v1/requirements",
        json={"project_id": project["id"], "title": "persist", "statement": "增加科学计数法"},
    ).json()["data"]
    created = client.post("/api/v1/workflows", json={"project_id": project["id"], "requirement_id": requirement["id"]})
    assert created.status_code == 200
    body = created.json()["data"]
    duplicate = client.post("/api/v1/workflows", json={"project_id": project["id"], "requirement_id": requirement["id"]})
    assert duplicate.status_code == 409
    with engine.connect() as connection:
        tables = {row[0] for row in connection.execute(text("select tablename from pg_tables where schemaname = 'public'"))}
        threads = connection.execute(text("select count(*) from checkpoints where thread_id = :id"), {"id": body["workflow_id"]}).scalar()
    assert {"execution_operations", "model_usages", "checkpoints", "checkpoint_blobs", "checkpoint_writes"} <= tables
    assert threads >= 1
    import subprocess as sp

    restarted = sp.run(["docker", "restart", "axiom-postgres-1"], check=False, capture_output=True, text=True)
    assert restarted.returncode == 0, restarted.stderr
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            with engine.connect() as connection:
                found = connection.execute(text("select name from projects where id = :id"), {"id": project["id"]}).scalar()
            if found == name:
                break
        except OperationalError:
            time.sleep(1)
    else:
        pytest.fail("project row was not readable after postgres restart")
    import asyncio

    app = create_app(settings, ctx)
    handler = app.exception_handlers[OperationalError]
    response = asyncio.run(handler(None, OperationalError("insert", {}, Exception("database down"))))
    assert response.status_code == 503
    body = response.body.decode()
    assert "database unavailable" in body
    assert "ase:ase" not in body
    with pytest.raises(Exception):
        create_app(
            Settings(
                database_url="postgresql+psycopg://ase:ase@127.0.0.1:5999/ase",
                workspace_root=tmp_path / "down",
                checkpoint_backend="sqlite",
            )
        )
