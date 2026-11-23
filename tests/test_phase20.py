import asyncio
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import func, select

from ase.adapters.scripted import ScriptedCodingAdapter
from ase.agents.base import BaseAgent
from ase.agents.cancellation import CancellationContext
from ase.agents.coding_agent import CodingAgent, coding_definition
from ase.agents.examples import EchoAgent, ResearchStubAgent, echo_definition, research_stub_definition
from ase.agents.lifecycle import assert_agent_transition
from ase.agents.registry import AgentRegistry
from ase.agents.runner import AgentRunner
from ase.app import build_context
from ase.contracts.domain import (
    AgentContext,
    AgentRunStatus,
    AgentSpec,
    Execution,
    ExecutionStatus,
    Project,
    Requirement,
    new_id,
    utcnow,
)
from ase.db.orm import AgentRunRow, RepositoryLeaseRow
from ase.db.session import session_scope
from ase.errors import (
    AgentAlreadyRegistered,
    AgentCancelledError,
    AgentExecutionError,
    AgentNotFoundError,
    AgentOutputValidationError,
    InputValidationError,
    WorkflowConflict,
)
from ase.infra.repository_lock import acquire_repository_lease, release_repository_lease, repository_identity
from ase.orchestration.nodes import bind_runner
from ase.orchestration.workflows.research_workflow import research_echo_workflow
from ase.contracts.domain import TestResult as SavedTestResult
from ase.store import get_agent_run, save_execution, save_project, save_requirement, save_test_result, save_workflow, update_workflow
from ase.workflow.graph import _test
from ase.workflow.runtime import bind_context, reset_context
from ase.workflow.service import WorkflowService


class BoomSandbox:
    def run_pytest(self, *args, **kwargs):
        raise AssertionError("pytest should be skipped")


class SlowAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "slow"

    @property
    def version(self) -> str:
        return "1"

    async def run(self, context, input_data):
        await asyncio.sleep(2)
        return await EchoAgent().run(context, input_data)


class BrokenOutput(BaseAgent):
    @property
    def name(self) -> str:
        return "broken"

    @property
    def version(self) -> str:
        return "1"

    async def run(self, context, input_data):
        from ase.contracts.domain import AgentResult

        return AgentResult(
            agent_name=self.name,
            agent_version=self.version,
            workflow_id=context.workflow_id,
            agent_run_id=context.agent_run_id,
            status=AgentRunStatus.COMPLETED,
            output={"text": 1},
        )


class ExplodingAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "explode"

    @property
    def version(self) -> str:
        return "1"

    async def run(self, context, input_data):
        raise RuntimeError("api_key=secret-value")


class FailingStub(BaseAgent):
    @property
    def name(self) -> str:
        return "research_stub"

    @property
    def version(self) -> str:
        return "1"

    async def run(self, context, input_data):
        raise RuntimeError("stub failed")


class CountingAdapter(ScriptedCodingAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def execute_task(self, task, session):
        self.calls += 1
        return await super().execute_task(task, session)


def _workflow(settings) -> tuple:
    ctx = build_context(settings)
    project = Project(name=f"p-{new_id()[:8]}", repo_path="E:/unused", base_ref="HEAD")
    requirement = Requirement(project_id=project.id, title="t", statement="need")
    workflow_id = new_id()
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
        save_workflow(session, workflow_id, project.id, requirement.id, "running")
    return ctx, project, workflow_id


def _registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(echo_definition(), EchoAgent)
    registry.register(research_stub_definition(), ResearchStubAgent)
    return registry


def test_agent_status_machine() -> None:
    assert_agent_transition("pending", "running")
    assert_agent_transition("running", "completed")
    with pytest.raises(ValueError):
        assert_agent_transition("completed", "running")
    with pytest.raises(ValueError):
        assert_agent_transition("pending", "completed")


def test_registry_registers_and_rejects_duplicates() -> None:
    registry = _registry()
    assert registry.is_registered("echo")
    assert registry.get_definition("echo").version == "1"
    assert "text" in registry.get_definition("echo").input_schema["required"]
    assert isinstance(registry.get("echo"), EchoAgent)
    with pytest.raises(AgentNotFoundError):
        registry.get("missing")
    with pytest.raises(AgentAlreadyRegistered):
        registry.register(echo_definition(), EchoAgent)


def test_runner_validates_persists_and_is_idempotent(settings) -> None:
    ctx, project, workflow_id = _workflow(settings)
    runner = AgentRunner(_registry(), ctx.sessions, settings)
    first = asyncio.run(
        runner.run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="echo",
            input_data={"text": "hello"},
            idempotency_key="echo-once",
        )
    )
    second = asyncio.run(
        runner.run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="echo",
            input_data={"text": "hello"},
            idempotency_key="echo-once",
        )
    )
    assert first.agent_run_id == second.agent_run_id
    assert first.output == {"text": "hello"}
    with session_scope(ctx.sessions) as session:
        stored = get_agent_run(session, first.agent_run_id)
        count = session.scalar(select(func.count()).select_from(AgentRunRow))
    assert stored is not None and stored.status == AgentRunStatus.COMPLETED
    assert stored.agent_version == "1"
    assert count == 1
    again = asyncio.run(
        runner.run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="echo",
            input_data={"text": "second"},
            idempotency_key="echo-twice",
        )
    )
    assert again.agent_run_id != first.agent_run_id


def test_runner_rejects_bad_input_output_timeout_and_secrets(settings) -> None:
    ctx, project, workflow_id = _workflow(settings)
    registry = _registry()
    slow = echo_definition().model_copy(update={"agent_id": "slow", "name": "Slow", "timeout_seconds": 1})
    broken = echo_definition().model_copy(update={"agent_id": "broken", "name": "Broken"})
    explode = echo_definition().model_copy(update={"agent_id": "explode", "name": "Explode"})
    registry.register(slow, SlowAgent)
    registry.register(broken, BrokenOutput)
    registry.register(explode, ExplodingAgent)
    runner = AgentRunner(registry, ctx.sessions, settings)
    with pytest.raises(InputValidationError):
        asyncio.run(runner.run(workflow_id=workflow_id, project_id=project.id, agent_name="echo", input_data={}))
    with pytest.raises(AgentNotFoundError):
        asyncio.run(runner.run(workflow_id=workflow_id, project_id=project.id, agent_name="missing", input_data={}))
    bad = asyncio.run(
        runner.run(workflow_id=workflow_id, project_id=project.id, agent_name="broken", input_data={"text": "x"})
    )
    assert bad.status == AgentRunStatus.FAILED
    assert bad.error_code == AgentOutputValidationError.code
    timed = asyncio.run(
        runner.run(workflow_id=workflow_id, project_id=project.id, agent_name="slow", input_data={"text": "x"})
    )
    assert timed.status == AgentRunStatus.TIMEOUT
    exploded = asyncio.run(
        runner.run(workflow_id=workflow_id, project_id=project.id, agent_name="explode", input_data={"text": "x"})
    )
    assert exploded.status == AgentRunStatus.FAILED
    assert "secret-value" not in (exploded.error_message or "")
    with session_scope(ctx.sessions) as session:
        row = get_agent_run(session, exploded.agent_run_id)
    assert "secret-value" not in (row.error_message or "")


def test_runner_records_cancellation(settings) -> None:
    ctx, project, workflow_id = _workflow(settings)
    with session_scope(ctx.sessions) as session:
        update_workflow(session, workflow_id, "cancelled", "cancelled")
    runner = AgentRunner(_registry(), ctx.sessions, settings)
    result = asyncio.run(
        runner.run(workflow_id=workflow_id, project_id=project.id, agent_name="echo", input_data={"text": "x"})
    )
    assert result.status == AgentRunStatus.CANCELLED
    context = AgentContext(
        workflow_id=workflow_id,
        agent_run_id=result.agent_run_id,
        project_id=project.id,
        correlation_id="c",
    )
    context.bind_cancellation(CancellationContext(ctx.sessions, workflow_id))
    with pytest.raises(AgentCancelledError):
        asyncio.run(context.raise_if_cancelled())


def test_repository_lock_conflicts_expires_and_checks_owner(settings, repo) -> None:
    ctx = build_context(settings)
    key = repository_identity(repo)
    other = repository_identity(Path(shutil.copytree(repo, settings.workspace_root / "other-repo")))
    with session_scope(ctx.sessions) as session:
        acquire_repository_lease(
            session,
            repository_key=key,
            holder_id="holder-a",
            workflow_id="wf-a",
            project_id="project-a",
            ttl_seconds=30,
        )
        acquire_repository_lease(
            session,
            repository_key=key,
            holder_id="holder-a",
            workflow_id="wf-a",
            project_id="project-a",
            ttl_seconds=30,
        )
        acquire_repository_lease(
            session,
            repository_key=other,
            holder_id="holder-b",
            workflow_id="wf-b",
            project_id="project-b",
            ttl_seconds=30,
        )
        with pytest.raises(WorkflowConflict):
            acquire_repository_lease(
                session,
                repository_key=key,
                holder_id="holder-b",
                workflow_id="wf-b",
                project_id="project-b",
                ttl_seconds=30,
            )
        count = session.scalar(select(func.count()).select_from(RepositoryLeaseRow).where(RepositoryLeaseRow.repository_key == key))
    assert count == 1
    past = utcnow() - timedelta(seconds=5)
    with session_scope(ctx.sessions) as session:
        acquire_repository_lease(
            session,
            repository_key=key,
            holder_id="holder-c",
            workflow_id="wf-c",
            project_id="project-c",
            ttl_seconds=30,
            now=past + timedelta(seconds=60),
        )
    with session_scope(ctx.sessions) as session:
        row = session.scalar(select(RepositoryLeaseRow).where(RepositoryLeaseRow.repository_key == key))
        row.expires_at = utcnow() - timedelta(seconds=1)
    with session_scope(ctx.sessions) as session:
        acquire_repository_lease(
            session,
            repository_key=key,
            holder_id="holder-d",
            workflow_id="wf-d",
            project_id="project-d",
            ttl_seconds=30,
        )
        with pytest.raises(WorkflowConflict):
            release_repository_lease(session, key, "holder-a")
        release_repository_lease(session, key, "holder-d")
        left = session.scalar(select(RepositoryLeaseRow).where(RepositoryLeaseRow.repository_key == key))
    assert left is None


def test_two_projects_on_one_repository_conflict(settings, repo) -> None:
    ctx = build_context(settings, sandbox=BoomSandbox())
    first = Project(name="same-1", repo_path=str(repo), base_ref="HEAD")
    second = Project(name="same-2", repo_path=str(repo), base_ref="HEAD")
    requirement_a = Requirement(project_id=first.id, title="a", statement="增加科学计数法")
    requirement_b = Requirement(project_id=second.id, title="b", statement="增加科学计数法")
    with session_scope(ctx.sessions) as session:
        save_project(session, first)
        save_project(session, second)
        save_requirement(session, requirement_a)
        save_requirement(session, requirement_b)
    service = WorkflowService(ctx)
    service.start(first.id, requirement_a.id, requirement_a.statement)
    with pytest.raises(WorkflowConflict) as caught:
        service.start(second.id, requirement_b.id, requirement_b.statement)
    assert "repository is locked" in str(caught.value)


def test_passed_test_result_is_reused(settings) -> None:
    ctx = build_context(settings, sandbox=BoomSandbox())
    project = Project(name="reuse", repo_path="E:/unused", base_ref="HEAD")
    requirement = Requirement(project_id=project.id, title="t", statement="need")
    workflow_id = new_id()
    execution = Execution(
        project_id=project.id,
        workflow_id=workflow_id,
        task_id=new_id(),
        agent_id="coding",
        status=ExecutionStatus.COMPLETED,
    )
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
        save_workflow(session, workflow_id, project.id, requirement.id, "testing")
        save_execution(session, execution)
        save_test_result(
            session,
            SavedTestResult(execution_id=execution.id, passed=True, command=["pytest"], exit_code=0, output="1 passed"),
        )
    token = bind_context(ctx)
    try:
        result = _test({"execution_id": execution.id, "project_id": project.id, "workflow_id": workflow_id, "workspace": {"path": "E:/unused"}})
    finally:
        reset_context(token)
    assert result["test_passed"] is True


def test_coding_agent_reuses_completed_operation(settings, tmp_path) -> None:
    ctx, project, workflow_id = _workflow(settings)
    adapter = CountingAdapter()
    registry = _registry()
    registry.register(coding_definition(), lambda: CodingAgent(adapter, ctx.sessions))
    runner = AgentRunner(registry, ctx.sessions, settings)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    task_id = new_id()
    payload = {"instruction": "edit", "workspace_path": str(workspace), "task_id": task_id}
    first = asyncio.run(
        runner.run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="coding",
            input_data=payload,
            task_id=task_id,
            idempotency_key="coding-1",
        )
    )
    marker = workspace / "calculator.py"
    assert marker.exists()
    marker.unlink()
    second = asyncio.run(
        runner.run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="coding",
            input_data=payload,
            task_id=task_id,
            idempotency_key="coding-2",
        )
    )
    assert adapter.calls == 1
    assert first.execution_ids == second.execution_ids
    assert not marker.exists()
    with session_scope(ctx.sessions) as session:
        stored = get_agent_run(session, second.agent_run_id)
    assert stored.execution_ids == first.execution_ids


def test_orchestration_passes_structured_output_and_resumes(settings, tmp_path) -> None:
    ctx, project, workflow_id = _workflow(settings)
    runner = AgentRunner(_registry(), ctx.sessions, settings)
    bind_runner(runner)
    connection = sqlite3.connect(tmp_path / "orch.sqlite", check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    paused = research_echo_workflow(saver, interrupt_before=["echo"])
    config = {"configurable": {"thread_id": workflow_id}}
    paused.invoke(
        {"workflow_id": workflow_id, "project_id": project.id, "requirement": "quant research", "agent_run_ids": []},
        config,
    )
    assert paused.get_state(config).next == ("echo",)
    resumed = research_echo_workflow(saver)
    final = resumed.invoke(None, config)
    assert final["echo_output"]["text"].startswith("stub report:")
    assert final["research_output"]["findings"] == ["architecture-only"]
    assert len(final["agent_run_ids"]) == 2
    with session_scope(ctx.sessions) as session:
        rows = session.scalars(select(AgentRunRow).where(AgentRunRow.workflow_id == workflow_id)).all()
    assert {row.agent_name for row in rows} == {"research_stub", "echo"}
    assert all(row.status == "completed" for row in rows)


def test_orchestration_failure_does_not_continue(settings, tmp_path) -> None:
    ctx, project, workflow_id = _workflow(settings)
    registry = AgentRegistry()
    registry.register(research_stub_definition(), FailingStub)
    registry.register(echo_definition(), EchoAgent)
    bind_runner(AgentRunner(registry, ctx.sessions, settings))
    connection = sqlite3.connect(tmp_path / "fail.sqlite", check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    graph = research_echo_workflow(saver)
    with pytest.raises(AgentExecutionError):
        graph.invoke(
            {"workflow_id": workflow_id, "project_id": project.id, "requirement": "fail", "agent_run_ids": []},
            {"configurable": {"thread_id": workflow_id}},
        )
    with session_scope(ctx.sessions) as session:
        rows = session.scalars(select(AgentRunRow).where(AgentRunRow.workflow_id == workflow_id)).all()
    assert [row.agent_name for row in rows] == ["research_stub"]
    assert rows[0].status == "failed"
    assert "secret" not in (rows[0].error_message or "")


def test_definition_rejects_blank_version() -> None:
    registry = AgentRegistry()
    spec = echo_definition().model_copy(update={"version": ""})
    with pytest.raises(ValueError):
        registry.register(spec, EchoAgent)
    unused = AgentSpec
    del unused
