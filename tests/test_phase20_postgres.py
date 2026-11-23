import os
import subprocess
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, OperationalError

from ase.app import build_context
from ase.config import Settings
from ase.contracts.domain import AgentRun, AgentRunStatus, Project, Requirement, new_id
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.infra.repository_lock import acquire_repository_lease, release_repository_lease
from ase.store import get_agent_run, save_agent_run, save_project, save_requirement, save_workflow

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"
EMPTY_URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase_phase20"


def _postgres():
    engine = create_engine(URL, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text("select 1"))
    except OperationalError as exc:
        pytest.skip(f"postgres is not reachable: {exc.__class__.__name__}")
    return engine


def _alembic(url: str) -> None:
    env = os.environ.copy()
    env["ASE_DATABASE_URL"] = url
    upgraded = subprocess.run(
        [".venv/Scripts/python.exe", "-m", "alembic", "upgrade", "head"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert upgraded.returncode == 0, upgraded.stderr


def test_empty_and_existing_database_keep_agent_runs() -> None:
    engine = _postgres()
    created = subprocess.run(
        ["docker", "exec", "axiom-postgres-1", "psql", "-U", "ase", "-d", "postgres", "-c", "DROP DATABASE IF EXISTS ase_phase20"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    created = subprocess.run(
        ["docker", "exec", "axiom-postgres-1", "psql", "-U", "ase", "-d", "postgres", "-c", "CREATE DATABASE ase_phase20"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    _alembic(EMPTY_URL)
    empty = create_engine(EMPTY_URL, pool_pre_ping=True)
    with empty.connect() as connection:
        tables = {row[0] for row in connection.execute(text("select tablename from pg_tables where schemaname = 'public'"))}
        version = connection.execute(text("select version_num from alembic_version")).scalar()
    assert version == "0004_phase23"
    assert {"agent_runs", "agent_run_executions", "repository_leases", "workflows"} <= tables

    _alembic(URL)
    with engine.connect() as connection:
        version = connection.execute(text("select version_num from alembic_version")).scalar()
        columns = {row[0] for row in connection.execute(text("select column_name from information_schema.columns where table_name = 'agent_runs'"))}
        approval_constraints = connection.execute(
            text("select conname from pg_constraint where conrelid = 'approvals'::regclass and contype = 'u'")
        ).scalars().all()
    assert version == "0004_phase23"
    assert "uq_approvals_workflow_subject" in approval_constraints
    assert {"workflow_id", "agent_name", "idempotency_key", "input_snapshot"} <= columns

    settings = Settings(database_url=URL, coding_runtime="scripted", checkpoint_backend="postgres")
    ctx = build_context(settings)
    project = Project(name=f"phase20-{os.urandom(3).hex()}", repo_path="E:/unused", base_ref="HEAD")
    requirement = Requirement(project_id=project.id, title="phase20", statement="persist agent run")
    workflow_id = new_id()
    run = AgentRun(
        workflow_id=workflow_id,
        project_id=project.id,
        agent_name="echo",
        agent_version="1",
        status=AgentRunStatus.COMPLETED,
        input_snapshot={"text": "kept"},
        output_snapshot={"text": "kept"},
        idempotency_key=f"phase20-{run_key()}",
    )
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
        save_workflow(session, workflow_id, project.id, requirement.id, "running")
        save_agent_run(session, run)
    with pytest.raises(IntegrityError):
        with session_scope(ctx.sessions) as session:
            save_agent_run(
                session,
                AgentRun(
                    workflow_id=new_id(),
                    project_id=project.id,
                    agent_name="echo",
                    agent_version="1",
                    idempotency_key=f"missing-{run_key()}",
                ),
            )
    with pytest.raises(RuntimeError):
        with session_scope(ctx.sessions) as session:
            save_agent_run(
                session,
                AgentRun(
                    workflow_id=workflow_id,
                    project_id=project.id,
                    agent_name="echo",
                    agent_version="1",
                    idempotency_key=f"rollback-{run_key()}",
                ),
            )
            raise RuntimeError("rollback")
    with session_scope(ctx.sessions) as session:
        stored = get_agent_run(session, run.id)
        acquire_repository_lease(
            session,
            repository_key="phase20-repo-a",
            holder_id="holder-a",
            workflow_id=workflow_id,
            project_id=project.id,
            ttl_seconds=30,
        )
        acquire_repository_lease(
            session,
            repository_key="phase20-repo-b",
            holder_id="holder-b",
            workflow_id=workflow_id,
            project_id=project.id,
            ttl_seconds=30,
        )
        with pytest.raises(WorkflowConflict):
            acquire_repository_lease(
                session,
                repository_key="phase20-repo-a",
                holder_id="holder-b",
                workflow_id=workflow_id,
                project_id=project.id,
                ttl_seconds=30,
            )
        release_repository_lease(session, "phase20-repo-b", "holder-b")
    assert stored is not None and stored.output_snapshot == {"text": "kept"}

    restarted = subprocess.run(["docker", "restart", "axiom-postgres-1"], check=False, capture_output=True, text=True)
    assert restarted.returncode == 0, restarted.stderr
    deadline = time.time() + 40
    found = None
    while time.time() < deadline:
        try:
            with engine.connect() as connection:
                found = connection.execute(text("select agent_name from agent_runs where id = :id"), {"id": run.id}).scalar()
            if found == "echo":
                break
        except OperationalError:
            time.sleep(1)
    assert found == "echo"


def run_key() -> str:
    return os.urandom(4).hex()
