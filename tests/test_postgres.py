import os
import subprocess

import pytest
from sqlalchemy import create_engine, text

from ase.app import build_context, create_app
from ase.config import Settings
from tests.doubles import GraphSandbox


URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"


def test_postgres_migration_and_workflow(repo, tmp_path) -> None:
    try:
        engine = create_engine(URL)
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except Exception as exc:
        pytest.skip(f"postgres is not reachable: {type(exc).__name__}")
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
    with engine.connect() as connection:
        names = {row[0] for row in connection.execute(text("select tablename from pg_tables where schemaname = 'public'"))}
    expected = {
        "projects",
        "requirements",
        "requirement_analyses",
        "development_plans",
        "tasks",
        "task_dependencies",
        "approvals",
        "workflows",
        "workflow_checkpoints",
        "executions",
        "execution_events",
        "artifacts",
        "test_results",
        "evaluation_reports",
        "agent_registry",
        "audit_logs",
    }
    assert expected <= names
    settings = Settings(
        database_url=URL,
        workspace_root=tmp_path / "workspaces",
        api_key="pg-key",
        coding_runtime="scripted",
        ase_source_root=repo,
    )
    ctx = build_context(settings, sandbox=GraphSandbox())
    from fastapi.testclient import TestClient

    client = TestClient(create_app(settings, ctx))
    project = client.post(
        "/api/v1/projects",
        json={"name": f"pg-{os.urandom(4).hex()}", "repo_path": str(repo), "base_ref": "HEAD"},
    ).json()["data"]
    requirement = client.post(
        "/api/v1/requirements",
        json={"project_id": project["id"], "title": "科学计数法", "statement": "增加科学计数法"},
    ).json()["data"]
    created = client.post("/api/v1/workflows", json={"project_id": project["id"], "requirement_id": requirement["id"]})
    assert created.status_code == 200
    body = created.json()["data"]
    assert body["status"] == "waiting_approval"
    denied = client.get("/api/v1/approvals")
    assert denied.status_code == 401
    approved = client.post(
        f"/api/v1/approvals/{body['approval_id']}/approve",
        headers={"X-ASE-API-KEY": "pg-key"},
        json={"comment": "ok"},
    )
    assert approved.status_code == 200
    delivered = approved.json()["data"]
    assert delivered["status"] == "delivered"
    execution = client.get(f"/api/v1/executions/{delivered['values']['execution_id']}")
    assert execution.status_code == 200
    with engine.connect() as connection:
        count = connection.execute(text("select count(*) from artifacts")).scalar()
    assert count and count > 0
