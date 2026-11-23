import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import ValidationError
from sqlalchemy import func, select

from ase.agents.registry import AgentRegistry
from ase.agents.research.agent import ResearchAgent, research_definition
from ase.agents.research.reader import ProjectReader
from ase.agents.research.schemas import (
    AcceptanceCriterion,
    CodeFinding,
    CriterionSource,
    Priority,
    RequirementAnalysis,
    ResearchReport,
    TechnicalRisk,
)
from ase.agents.runner import AgentRunner
from ase.app import build_context, create_app
from ase.contracts.domain import AgentRun, AgentRunStatus, Project, Requirement, new_id, utcnow
from ase.db.orm import AgentRunRow, ArtifactRow, ExecutionRow, ModelUsageRow
from ase.db.session import session_scope
from ase.errors import ProjectReadError, WorkflowConflict
from ase.orchestration.research_graph import build_research_graph
from ase.orchestration.research_nodes import bind_research_runner, reset_research_runner
from ase.orchestration.research_service import ResearchWorkflowService
from ase.store import save_project, save_requirement, save_workflow


def _assessment(path: str = "calculator.py", findings: bool = True) -> dict:
    finding = []
    if findings:
        finding = [
            {
                "finding_id": "f1",
                "title": "evaluate is unimplemented",
                "description": "The function raises NotImplementedError.",
                "file_path": path,
                "start_line": 5,
                "end_line": 6,
                "relevance": "The requirement needs expression evaluation.",
                "evidence": "raise NotImplementedError(expression)",
                "confidence": 0.9,
            }
        ]
    return {
        "requirement_analysis": {
            "summary": "Add scientific notation evaluation.",
            "objectives": ["Support scientific notation in evaluate"],
            "functional_requirements": ["evaluate('1e2') returns 100"],
            "non_functional_requirements": [],
            "constraints": ["Do not invent business rules"],
            "assumptions": [],
            "clarification_questions": [],
        },
        "code_findings": finding,
        "existing_capabilities": ["add"],
        "missing_capabilities": ["scientific notation"],
        "technical_recommendations": ["Implement a small expression parser"],
        "technical_risks": [
            {
                "title": "eval is unsafe",
                "description": "A direct eval would execute arbitrary code.",
                "severity": "high",
                "likelihood": "medium",
                "affected_modules": ["calculator.py"],
                "mitigation": "Parse a restricted expression grammar.",
            }
        ],
        "acceptance_criteria": [
            {
                "criterion_id": "a1",
                "description": "Scientific notation expressions return a float.",
                "verification_method": "pytest",
                "priority": "must",
                "source": "user_requirement",
            }
        ],
        "clarification_questions": [],
        "research_summary": "evaluate still raises NotImplementedError.",
        "limitations": [],
    }


class ScriptModel:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, settings, system: str, user: str, json_mode: bool = False):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        usage = {"prompt_tokens": 11, "completion_tokens": 5, "elapsed_seconds": 0.2, "provider": "test", "model": "fake"}
        return json.dumps(item), {"usage": usage, "elapsed_seconds": 0.2, "provider": "test", "model": "fake"}


def _workflow(settings, repo: Path):
    ctx = build_context(settings)
    project = Project(name=f"research-{new_id()[:8]}", repo_path=str(repo), base_ref="HEAD")
    requirement = Requirement(project_id=project.id, title="research", statement="evaluate scientific notation")
    workflow_id = new_id()
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
        save_workflow(session, workflow_id, project.id, requirement.id, "running")
    return ctx, project, workflow_id


def _runner(ctx, model: ScriptModel) -> AgentRunner:
    registry = AgentRegistry()
    registry.register(research_definition(), lambda: ResearchAgent(ctx.settings, ctx.sessions, complete_fn=model))
    return AgentRunner(registry, ctx.sessions, ctx.settings)


def test_research_schema_rejects_bad_values() -> None:
    RequirementAnalysis(summary="need evaluate")
    with pytest.raises(ValidationError):
        RequirementAnalysis(summary="need", extra_field="no")
    with pytest.raises(ValidationError):
        RequirementAnalysis(summary="x" * 2001)
    with pytest.raises(ValidationError):
        TechnicalRisk(
            title="risk",
            description="detail",
            severity="urgent",
            likelihood="low",
            affected_modules=[],
            mitigation="fix",
        )
    with pytest.raises(ValidationError):
        CodeFinding(
            finding_id="f",
            title="t",
            description="d",
            file_path="C:/outside.txt",
            relevance="r",
            evidence="e",
            confidence=0.2,
        )
    with pytest.raises(ValidationError):
        AcceptanceCriterion(
            criterion_id="a",
            description="d",
            verification_method="pytest",
            priority="must",
            source="guessed",
        )
    report = ResearchReport(
        schema_version="1.0",
        requirement_analysis=RequirementAnalysis(summary="need"),
        project_analysis={
            "project_summary": "files",
            "languages": ["Python"],
            "frameworks": [],
            "dependencies": [],
            "architecture_description": "from file extensions",
            "entry_points": [],
            "test_frameworks": [],
            "database_technologies": [],
            "relevant_modules": [],
        },
        research_summary="done",
    )
    assert report.model_dump(mode="json")["schema_version"] == "1.0"


def test_project_reader_is_read_only_and_bounded(tmp_path: Path, repo: Path) -> None:
    reader = ProjectReader("project-a", repo, max_depth=2, max_files=20, max_results=1)
    tree = asyncio.run(reader.get_project_tree("project-a"))
    assert "calculator.py" in tree["files"]
    loaded = asyncio.run(reader.read_file("project-a", "calculator.py"))
    assert "NotImplementedError" in loaded["content"]
    with pytest.raises(ProjectReadError) as missing:
        asyncio.run(reader.read_file("project-a", "missing.py"))
    assert missing.value.code == "file_not_found"
    with pytest.raises(ProjectReadError) as escaped:
        asyncio.run(reader.read_file("project-a", "../secret.txt"))
    assert escaped.value.code == "path_escape"
    (repo / ".env").write_text("ASE_LLM_API_KEY=hidden", encoding="utf-8")
    with pytest.raises(ProjectReadError) as sensitive:
        asyncio.run(reader.read_file("project-a", ".env"))
    assert sensitive.value.code == "sensitive_file"
    huge = repo / "huge.txt"
    huge.write_text("x" * 40, encoding="utf-8")
    limited = ProjectReader("project-a", repo, max_bytes=32)
    with pytest.raises(ProjectReadError) as large:
        asyncio.run(limited.read_file("project-a", "huge.txt"))
    assert large.value.code == "file_too_large"
    nested = repo / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "deep.txt").write_text("deep", encoding="utf-8")
    shallow = asyncio.run(reader.get_project_tree("project-a", max_depth=1))
    assert "a/b/c/deep.txt" not in shallow["files"]
    (repo / "one.py").write_text("token\n", encoding="utf-8")
    (repo / "two.py").write_text("token\n", encoding="utf-8")
    found = asyncio.run(reader.search_code("project-a", "token"))
    assert len(found["matches"]) == 1
    assert found["truncated"] is True
    with pytest.raises(ProjectReadError) as forbidden:
        asyncio.run(reader.read_file("project-b", "calculator.py"))
    assert forbidden.value.code == "project_forbidden"
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = repo / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        link = None
    if link is not None:
        with pytest.raises(ProjectReadError) as linked:
            asyncio.run(reader.read_file("project-a", "linked.txt"))
        assert linked.value.code == "path_escape"
    status = asyncio.run(reader.get_git_status("project-a"))
    assert status["available"] is True
    assert status["revision"]

    class Changing(ProjectReader):
        def _stamp(self, path: Path) -> int:
            self._ticks = getattr(self, "_ticks", 0) + 1
            return self._ticks

    changing = Changing("project-a", repo, max_bytes=100_000)
    stale = asyncio.run(changing.read_file("project-a", "calculator.py"))
    assert stale["stale"] is True


def test_research_agent_persists_report_without_execution(settings, repo: Path) -> None:
    ctx, project, workflow_id = _workflow(settings, repo)
    model = ScriptModel([_assessment()])
    runner = _runner(ctx, model)
    result = asyncio.run(
        runner.run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="research",
            input_data={"requirement": "evaluate scientific notation"},
            idempotency_key="research-once",
        )
    )
    again = asyncio.run(
        runner.run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="research",
            input_data={"requirement": "evaluate scientific notation"},
            idempotency_key="research-once",
        )
    )
    assert model.calls == 1
    assert result.agent_run_id == again.agent_run_id
    assert result.execution_ids == []
    assert result.status == AgentRunStatus.COMPLETED, result.error_message
    assert result.output["schema_version"] == "1.0"
    assert result.output["code_findings"][0]["file_path"] == "calculator.py"
    assert "Python" in result.output["project_analysis"]["languages"]
    with session_scope(ctx.sessions) as session:
        stored = session.get(AgentRunRow, result.agent_run_id)
        artifact = session.get(ArtifactRow, result.output_artifact_ids[0])
        usage = session.scalar(select(ModelUsageRow).where(ModelUsageRow.agent_run_id == result.agent_run_id))
        executions = session.scalar(select(func.count()).select_from(ExecutionRow).where(ExecutionRow.workflow_id == workflow_id))
    assert stored.status == "completed"
    assert stored.input_snapshot["requirement"] == "evaluate scientific notation"
    assert stored.output_snapshot["research_summary"].startswith("evaluate")
    assert artifact.kind == "research_report"
    body = json.loads(artifact.body)
    assert body["agent_run_id"] == result.agent_run_id
    assert body["schema_version"] == "1.0"
    assert usage.prompt_tokens == 11
    assert usage.completion_tokens == 5
    assert usage.estimated_cost is None
    assert usage.agent_name == "research"
    assert executions == 0
    assert "openhands" not in Path(ResearchAgent.__module__.replace(".", "/") + ".py").read_text(encoding="utf-8") if False else "openhands" not in Path(__file__).parents[1].joinpath("src/ase/agents/research/agent.py").read_text(encoding="utf-8")


def test_research_agent_handles_search_miss_parse_failure_and_http_error(settings, repo: Path) -> None:
    ctx, project, workflow_id = _workflow(settings, repo)
    empty = ScriptModel([_assessment(findings=False)])
    runner = _runner(ctx, empty)
    missed = asyncio.run(
        runner.run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="research",
            input_data={"requirement": "zzzzunique"},
            idempotency_key="research-miss",
        )
    )
    assert missed.output["code_findings"] == []
    assert any("没有命中" in item for item in missed.output["limitations"])
    broken = ScriptModel(["not-json", _assessment()])
    repaired = asyncio.run(
        _runner(ctx, broken).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="research",
            input_data={"requirement": "evaluate scientific notation"},
            idempotency_key="research-repair",
        )
    )
    assert repaired.status == AgentRunStatus.COMPLETED
    assert broken.calls == 2
    failing = ScriptModel(["not-json", "still-bad", "nope"])
    failed = asyncio.run(
        _runner(ctx, failing).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="research",
            input_data={"requirement": "evaluate scientific notation"},
            idempotency_key="research-bad-json",
        )
    )
    assert failed.status == AgentRunStatus.FAILED
    assert failing.calls == 3
    http = ScriptModel([RuntimeError("model HTTP 503")])
    http_result = asyncio.run(
        _runner(ctx, http).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="research",
            input_data={"requirement": "evaluate scientific notation"},
            idempotency_key="research-http",
        )
    )
    assert http_result.status == AgentRunStatus.FAILED
    assert "503" in (http_result.error_message or "")
    repeated = asyncio.run(
        _runner(ctx, http).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="research",
            input_data={"requirement": "evaluate scientific notation"},
            idempotency_key="research-http",
        )
    )
    assert repeated.status == AgentRunStatus.FAILED
    assert http.calls == 1
    invented = _assessment(path="missing_module.py")
    dropped = asyncio.run(
        _runner(ctx, ScriptModel([invented])).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="research",
            input_data={"requirement": "evaluate scientific notation"},
            idempotency_key="research-drop",
        )
    )
    assert dropped.output["code_findings"] == []
    assert any("missing_module.py" in item for item in dropped.output["limitations"])
    shifted = _assessment()
    shifted["code_findings"][0]["start_line"] = 1
    shifted["code_findings"][0]["evidence"] = "raise NotImplementedError(expression)"
    corrected = asyncio.run(
        _runner(ctx, ScriptModel([shifted])).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="research",
            input_data={"requirement": "evaluate scientific notation"},
            idempotency_key="research-line",
        )
    )
    assert corrected.output["code_findings"][0]["start_line"] == 6


def test_running_agent_run_can_resume(settings, repo: Path) -> None:
    ctx, project, workflow_id = _workflow(settings, repo)
    run = AgentRun(
        workflow_id=workflow_id,
        project_id=project.id,
        agent_name="research",
        agent_version="1",
        status=AgentRunStatus.RUNNING,
        idempotency_key="research-resume",
        started_at=utcnow(),
        input_snapshot={"requirement": "evaluate scientific notation"},
    )
    with session_scope(ctx.sessions) as session:
        from ase.store import save_agent_run

        save_agent_run(session, run)
    model = ScriptModel([_assessment()])
    result = asyncio.run(
        _runner(ctx, model).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="research",
            input_data={"requirement": "evaluate scientific notation"},
            idempotency_key="research-resume",
        )
    )
    assert model.calls == 1
    assert result.agent_run_id == run.id
    assert result.status == AgentRunStatus.COMPLETED


def test_research_workflow_checkpoint_resume_and_api(settings, repo: Path, tmp_path: Path) -> None:
    ctx, project, workflow_id = _workflow(settings, repo)
    model = ScriptModel([_assessment()])
    runner = _runner(ctx, model)
    connection = sqlite3.connect(tmp_path / "research.sqlite", check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    paused = build_research_graph(saver, interrupt_before=["research"])
    token = bind_research_runner(runner)
    config = {"configurable": {"thread_id": workflow_id}}
    try:
        paused.invoke(
            {
                "workflow_id": workflow_id,
                "project_id": project.id,
                "requirement": "evaluate scientific notation",
                "research_key": "graph-research-1",
                "agent_run_ids": [],
                "include_tests": True,
                "include_dependencies": True,
                "max_files": 100,
                "max_context_chars": 50000,
            },
            config,
        )
        assert paused.get_state(config).next == ("research",)
        assert model.calls == 0
        final = build_research_graph(saver).invoke(None, config)
    finally:
        reset_research_runner(token)
    assert model.calls == 1
    assert final["research_report"]["schema_version"] == "1.0"
    assert final["artifact_ids"]
    from ase.store import get_workflow, update_workflow

    with session_scope(ctx.sessions) as session:
        update_workflow(session, workflow_id, "failed")
    failing = ScriptModel([RuntimeError("model HTTP 500")])
    service = ResearchWorkflowService(ctx, checkpointer=MemorySaver(), complete_fn=failing)
    with pytest.raises(Exception):
        service.start(project.id, "evaluate scientific notation")
    with session_scope(ctx.sessions) as session:
        failed_run = session.scalar(select(AgentRunRow).where(AgentRunRow.status == "failed"))
        failed = get_workflow(session, failed_run.workflow_id)
    assert failed.status == "failed"
    good = ResearchWorkflowService(ctx, checkpointer=MemorySaver(), complete_fn=ScriptModel([_assessment()]))
    started = good.start(project.id, "evaluate scientific notation", {"max_files": 20})
    assert started["status"] == "researched"
    assert started["agent_run_id"]
    assert started["research_artifact_id"]
    other = Project(name="busy", repo_path=str(repo), base_ref="HEAD")
    other_requirement = Requirement(project_id=other.id, title="busy", statement="busy")
    with session_scope(ctx.sessions) as session:
        save_project(session, other)
        save_requirement(session, other_requirement)
        save_workflow(session, new_id(), other.id, other_requirement.id, "running")
    with pytest.raises(WorkflowConflict):
        good.start(other.id, "evaluate scientific notation")
    app = create_app(settings, ctx)
    app.state.research = ResearchWorkflowService(ctx, checkpointer=MemorySaver(), complete_fn=ScriptModel([_assessment()]))
    client = TestClient(app)
    response = client.post(
        "/api/v1/research-workflows",
        json={"project_id": project.id, "requirement": "add logging", "research_options": {"include_tests": True}},
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["research_artifact_id"]
    assert "api_key" not in response.text
    follow = client.get(f"/api/v1/research-workflows/{body['workflow_id']}")
    assert follow.status_code == 200
    assert follow.json()["data"]["status"] == "researched"
