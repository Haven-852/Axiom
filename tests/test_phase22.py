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

from ase.agents.planning_agent import PlanningAgent, planning_definition
from ase.agents.registry import AgentRegistry
from ase.agents.research.schemas import ResearchReport
from ase.agents.runner import AgentRunner
from ase.app import build_context, create_app
from ase.contracts.domain import AgentRun, AgentRunStatus, Artifact, Project, Requirement, new_id
from ase.db.orm import DevelopmentPlanRow, ExecutionRow, ModelUsageRow, TaskRow
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.orchestration.planning_graph import build_planning_graph
from ase.orchestration.planning_nodes import bind_planning_runner, reset_planning_runner
from ase.planning.dag_validator import finalize_plan, validate_plan
from ase.planning.research_input import PlanningInputError, load_planning_input, planning_key
from ase.planning.schemas import ClarificationStatus, Plan, PlanPriority, PlanTask, PlanValidationResult
from ase.planning.service import PlanningWorkflowService
from ase.planning.task_builder import persist_plan
from ase.store import active_workflow_id, get_workflow, save_agent_run, save_artifact, save_project, save_requirement, save_workflow
from ase.workflow.service import WorkflowService


def _report(questions: list[str] | None = None) -> ResearchReport:
    if questions is None:
        questions = ["科学计数法只支持字面量，还是完整表达式？"]
    return ResearchReport.model_validate(
        {
            "schema_version": "1.0",
            "requirement_analysis": {
                "summary": "Add scientific notation evaluation.",
                "objectives": ["Support scientific notation"],
                "functional_requirements": ["evaluate accepts a confirmed syntax"],
                "non_functional_requirements": [],
                "constraints": ["Do not invent the syntax scope"],
                "assumptions": [],
                "clarification_questions": questions,
            },
            "project_analysis": {
                "project_summary": "Small Python calculator.",
                "languages": ["Python"],
                "frameworks": [],
                "dependencies": [],
                "architecture_description": "One module and one test file.",
                "entry_points": ["calculator.py"],
                "test_frameworks": [],
                "database_technologies": [],
                "relevant_modules": ["calculator.py"],
            },
            "code_findings": [
                {
                    "finding_id": "CF-001",
                    "title": "evaluate is unimplemented",
                    "description": "evaluate raises NotImplementedError.",
                    "file_path": "calculator.py",
                    "start_line": 5,
                    "end_line": 6,
                    "relevance": "The missing behavior is here.",
                    "evidence": "raise NotImplementedError(expression)",
                    "confidence": 0.9,
                },
                {
                    "finding_id": "CF-003",
                    "title": "README asks for scientific notation",
                    "description": "The task statement is in the README.",
                    "file_path": "README.md",
                    "start_line": 3,
                    "end_line": 3,
                    "relevance": "This is the user-facing task.",
                    "evidence": "scientific notation",
                    "confidence": 0.8,
                },
            ],
            "existing_capabilities": ["add is implemented"],
            "missing_capabilities": ["evaluate"],
            "technical_recommendations": [],
            "technical_risks": [],
            "acceptance_criteria": [
                {
                    "criterion_id": "AC-001",
                    "description": "The README task is addressed after the scope is confirmed.",
                    "verification_method": "review",
                    "priority": "must",
                    "source": "user_requirement",
                },
                {
                    "criterion_id": "AC-006",
                    "description": "A parser might be needed.",
                    "verification_method": "review",
                    "priority": "could",
                    "source": "inferred",
                },
            ],
            "clarification_questions": questions,
            "research_summary": "evaluate is unimplemented and the notation scope is open.",
            "limitations": ["Only excerpts were read."],
        }
    )


def _draft() -> dict:
    return {
        "planning_summary": "Split documentation from the blocked implementation.",
        "tasks": [
            {
                "task_id": "docs",
                "title": "Record the open notation question",
                "description": "Record that the notation scope is unresolved.",
                "task_type": "documentation",
                "priority": "medium",
                "dependencies": [],
                "related_files": ["README.md"],
                "acceptance_criteria": ["The open question is recorded without choosing a syntax."],
                "estimated_complexity": "small",
                "source_findings": ["CF-003"],
                "source_acceptance_criteria": ["AC-001"],
                "assumptions": [],
                "blocking_clarification_ids": [],
            },
            {
                "task_id": "impl",
                "title": "Implement evaluate after the scope is known",
                "description": "Change evaluate only after the notation question is answered.",
                "task_type": "implementation",
                "priority": "high",
                "dependencies": ["docs"],
                "related_files": ["calculator.py"],
                "acceptance_criteria": ["evaluate matches the later confirmed syntax."],
                "estimated_complexity": "medium",
                "source_findings": ["CF-001"],
                "source_acceptance_criteria": ["AC-006"],
                "assumptions": [],
                "blocking_clarification_ids": [],
            },
        ],
        "clarification_items": [],
        "risks": [
            {
                "risk_id": "r1",
                "title": "Scope may change the parser",
                "description": "A full expression grammar is a different task from literal parsing.",
                "severity": "high",
                "likelihood": "medium",
                "mitigation": "Keep the implementation blocked until the question is answered.",
                "affected_task_ids": ["impl"],
                "source": "research_report",
            }
        ],
        "assumptions": ["The notation scope is not confirmed."],
        "limitations": ["Planning did not rescan the repository."],
    }


def _empty_validation(count: int = 1) -> dict:
    return {
        "valid": False,
        "structurally_valid": False,
        "errors": [],
        "warnings": [],
        "task_count": count,
        "blocked_task_count": 0,
        "executable_task_count": 0,
        "topological_order": [],
    }


def _task(task_id: str, **overrides) -> dict:
    data = {
        "task_id": task_id,
        "title": task_id,
        "description": "A documented task.",
        "task_type": "documentation",
        "priority": "low",
        "dependencies": [],
        "related_files": ["README.md"],
        "acceptance_criteria": ["The note exists."],
        "estimated_complexity": "small",
        "source_findings": ["CF-003"],
        "source_acceptance_criteria": ["AC-001"],
        "assumptions": [],
        "blocking_clarification_ids": [],
    }
    data.update(overrides)
    return data


def _plan(tasks: list[dict], questions: list[str] | None = None, order: list[str] | None = None) -> tuple[Plan, ResearchReport]:
    report = _report(questions)
    plan = Plan.model_validate(
        {
            "schema_version": "1.0",
            "plan_id": "plan-1",
            "source_research_artifact_id": "artifact-1",
            "source_research_agent_run_id": "run-1",
            "requirement_summary": report.requirement_analysis.summary,
            "planning_summary": "Manual plan for validation.",
            "tasks": tasks,
            "clarification_items": [],
            "risks": [],
            "execution_order": order or [],
            "validation_result": _empty_validation(len(tasks)),
            "assumptions": [],
            "limitations": [],
        }
    )
    return plan, report


class ScriptModel:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, settings, system: str, user: str, json_mode: bool = False):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        usage = {"prompt_tokens": 11, "completion_tokens": 5}
        return text, {"usage": usage, "elapsed_seconds": 0.2, "provider": "test", "model": "fake"}


def _workflow(settings, status: str = "researched"):
    ctx = build_context(settings)
    project = Project(name=f"plan-{new_id()[:8]}", repo_path="E:/unused", base_ref="HEAD")
    requirement = Requirement(project_id=project.id, title="plan", statement="scientific notation")
    workflow_id = new_id()
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
        save_workflow(session, workflow_id, project.id, requirement.id, status)
    return ctx, project, requirement, workflow_id


def _seed(ctx, project, workflow_id: str, report: ResearchReport | None = None, *, kind: str = "research_report", agent_name: str = "research"):
    report = report or _report()
    run = AgentRun(
        workflow_id=workflow_id,
        project_id=project.id,
        agent_name=agent_name,
        agent_version="1",
        status=AgentRunStatus.COMPLETED,
        output_snapshot={"schema_version": "1.0"},
    )
    body = {
        "schema_version": "1.0",
        "agent_run_id": run.id,
        "workflow_id": workflow_id,
        "project_id": project.id,
        "revision": "54a0303761a57d1e572c8304881975134b3b594a",
        "files_read": 3,
        "search_results": 3,
        "content": report.model_dump(mode="json"),
    }
    artifact = Artifact(
        project_id=project.id,
        workflow_id=workflow_id,
        kind=kind,
        uri=f"research://{run.id}",
        body=json.dumps(body, ensure_ascii=False),
    )
    with session_scope(ctx.sessions) as session:
        save_agent_run(session, run)
        save_artifact(session, artifact)
    return run, artifact


def _runner(ctx, model: ScriptModel) -> AgentRunner:
    registry = AgentRegistry()
    registry.register(planning_definition(), lambda: PlanningAgent(ctx.settings, ctx.sessions, complete_fn=model))
    return AgentRunner(registry, ctx.sessions, ctx.settings)


def test_plan_schema_rejects_bad_values() -> None:
    plan, _report_ok = _plan([_task("docs")])
    restored = Plan.model_validate(json.loads(plan.model_dump_json()))
    assert restored.schema_version == "1.0"
    assert restored.tasks[0].task_id == "docs"
    with pytest.raises(ValidationError):
        PlanTask.model_validate(_task("docs", priority="urgent"))
    with pytest.raises(ValidationError):
        PlanTask.model_validate(_task("docs", estimated_complexity="huge"))
    with pytest.raises(ValidationError):
        PlanTask.model_validate(_task("docs", title=""))
    with pytest.raises(ValidationError):
        PlanTask.model_validate(_task("docs", related_files=["../secret.py"]))
    with pytest.raises(ValidationError):
        Plan.model_validate({**plan.model_dump(mode="json"), "tasks": []})
    assert ClarificationStatus("answered") == ClarificationStatus.ANSWERED
    with pytest.raises(ValueError):
        ClarificationStatus("closed")
    assert PlanPriority.HIGH == "high"


def test_research_input_reads_the_phase21_envelope(settings) -> None:
    ctx, project, _requirement, workflow_id = _workflow(settings)
    _run, artifact = _seed(ctx, project, workflow_id)
    with session_scope(ctx.sessions) as session:
        loaded = load_planning_input(session, workflow_id, artifact.id)
    assert loaded.research_report.schema_version == "1.0"
    assert loaded.repository_revision == "54a0303761a57d1e572c8304881975134b3b594a"
    assert loaded.research_agent_run_id
    first = planning_key(loaded)
    assert planning_key(loaded) == first
    assert planning_key(loaded, agent_version="2") != first

    other_ctx, other_project, _other_requirement, other_workflow = _workflow(settings)
    _other_run, other_artifact = _seed(other_ctx, other_project, other_workflow)
    with session_scope(ctx.sessions) as session:
        with pytest.raises(PlanningInputError) as missing:
            load_planning_input(session, workflow_id, "missing-artifact")
        assert missing.value.code == "artifact_not_found"
        with pytest.raises(PlanningInputError) as mismatch:
            load_planning_input(session, workflow_id, other_artifact.id)
        assert mismatch.value.code == "artifact_workflow_mismatch"

    broken = _report().model_dump(mode="json")
    del broken["research_summary"]
    _bad_run, bad_artifact = _seed(ctx, project, workflow_id, ResearchReport.model_validate(_report().model_dump(mode="json")))
    with session_scope(ctx.sessions) as session:
        from ase.db.orm import ArtifactRow

        row = session.get(ArtifactRow, bad_artifact.id)
        payload = json.loads(row.body)
        payload["content"] = broken
        row.body = json.dumps(payload)
    with session_scope(ctx.sessions) as session:
        with pytest.raises(PlanningInputError) as invalid:
            load_planning_input(session, workflow_id, bad_artifact.id)
        assert invalid.value.code == "research_report_invalid"

    _wrong_run, wrong_artifact = _seed(ctx, project, workflow_id, agent_name="coding")
    with session_scope(ctx.sessions) as session:
        with pytest.raises(PlanningInputError) as wrong:
            load_planning_input(session, workflow_id, wrong_artifact.id)
        assert wrong.value.code == "research_run_mismatch"


def test_dag_validator_accepts_blocked_work_and_rejects_cycles() -> None:
    plan, report = _plan([_task("docs"), _task("impl", task_type="implementation", dependencies=["docs"], related_files=["calculator.py"], source_findings=["CF-001"], source_acceptance_criteria=["AC-006"], assumptions=["验收标准 AC-006 来自推断，不是已确认需求。"])])
    sealed, result = finalize_plan(plan, report)
    assert result.valid is True
    assert result.structurally_valid is True
    assert result.blocked_task_count == 1
    assert result.executable_task_count == 1
    assert sealed.execution_order == ["docs"]
    assert "impl" in result.topological_order

    independent, independent_report = _plan([_task("only")], questions=[])
    sealed_only, only = finalize_plan(independent, independent_report)
    assert only.valid is True
    assert sealed_only.execution_order == ["only"]

    layered, layered_report = _plan(
        [_task("a"), _task("b", dependencies=["a"]), _task("c", dependencies=["b"])],
        questions=[],
    )
    _sealed_layered, layered_result = finalize_plan(layered, layered_report)
    assert layered_result.topological_order == ["a", "b", "c"]

    missing, missing_report = _plan([_task("a", dependencies=["missing"])], questions=[])
    _sealed_missing, missing_result = finalize_plan(missing, missing_report)
    assert missing_result.valid is False
    assert any("missing" in item for item in missing_result.errors)

    self_dep, self_report = _plan([_task("a", dependencies=["a"])], questions=[])
    _sealed_self, self_result = finalize_plan(self_dep, self_report)
    assert any("itself" in item for item in self_result.errors)

    cycle, cycle_report = _plan([_task("a", dependencies=["b"]), _task("b", dependencies=["a"])], questions=[])
    _sealed_cycle, cycle_result = finalize_plan(cycle, cycle_report)
    assert cycle_result.valid is False
    assert any("cycle" in item for item in cycle_result.errors)

    duplicated, duplicated_report = _plan([_task("a"), _task("a")], questions=[])
    _sealed_dup, dup_result = finalize_plan(duplicated, duplicated_report)
    assert any("duplicated" in item for item in dup_result.errors)

    bad_order, bad_report = _plan([_task("a"), _task("b", dependencies=["a"])], questions=[], order=["b", "a"])
    bad = validate_plan(bad_order, bad_report)
    assert bad.valid is False

    unknown_finding, finding_report = _plan([_task("a", source_findings=["NOPE"])], questions=[])
    _sealed_finding, finding_result = finalize_plan(unknown_finding, finding_report)
    assert any("NOPE" in item for item in finding_result.errors)

    unknown_criterion, criterion_report = _plan([_task("a", source_acceptance_criteria=["NOPE"])], questions=[])
    _sealed_criterion, criterion_result = finalize_plan(unknown_criterion, criterion_report)
    assert any("NOPE" in item for item in criterion_result.errors)

    decided, decided_report = _plan([_task("a", description="用户已确认只支持字面量。")], questions=[])
    _sealed_decided, decided_result = finalize_plan(decided, decided_report)
    assert decided_result.valid is False


def test_task_builder_is_idempotent_and_rolls_back(settings) -> None:
    ctx, _project, requirement, _workflow_id = _workflow(settings)
    plan, report = _plan(
        [
            _task("docs"),
            _task(
                "impl",
                task_type="implementation",
                dependencies=["docs"],
                related_files=["calculator.py"],
                source_findings=["CF-001"],
                source_acceptance_criteria=["AC-001"],
            ),
        ]
    )
    sealed, result = finalize_plan(plan, report)
    assert result.valid is True
    with pytest.raises(RuntimeError, match="persist failed"):
        persist_plan(ctx.sessions, requirement.id, sealed, fail=True)
    with session_scope(ctx.sessions) as session:
        assert session.scalar(select(func.count()).select_from(DevelopmentPlanRow)) == 0
    persist_plan(ctx.sessions, requirement.id, sealed)
    persist_plan(ctx.sessions, requirement.id, sealed)
    with session_scope(ctx.sessions) as session:
        tasks = session.scalars(select(TaskRow).where(TaskRow.plan_id == sealed.plan_id)).all()
        from ase.db.orm import TaskDependencyRow

        edges = session.scalars(select(TaskDependencyRow).where(TaskDependencyRow.plan_id == sealed.plan_id)).all()
    assert len(tasks) == 2
    by_id = {item.id: item for item in tasks}
    assert by_id["impl"].status == "blocked"
    assert by_id["docs"].status == "ready"
    assert by_id["impl"].agent_id == "implementation"
    assert by_id["docs"].agent_id != "coding"
    assert {(item.predecessor_id, item.successor_id) for item in edges} == {("docs", "impl")}
    assert sealed.tasks[1].acceptance_criteria
    assert sealed.tasks[1].source_findings == ["CF-001"]


def test_planning_agent_persists_usage_and_reuses_a_completed_run(settings) -> None:
    ctx, project, _requirement, workflow_id = _workflow(settings)
    _run, artifact = _seed(ctx, project, workflow_id)
    model = ScriptModel([_draft()])
    runner = _runner(ctx, model)
    with session_scope(ctx.sessions) as session:
        loaded = load_planning_input(session, workflow_id, artifact.id)
    result = asyncio.run(
        runner.run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="planning",
            input_data={"research_artifact_id": artifact.id},
            idempotency_key=planning_key(loaded),
        )
    )
    assert result.status == AgentRunStatus.COMPLETED
    assert model.calls == 1
    assert result.output["validation_result"]["valid"] is True
    assert result.output["validation_result"]["blocked_task_count"] >= 1
    assert all(item["status"] == "unresolved" for item in result.output["clarification_items"])
    decided = json.dumps(result.output["tasks"], ensure_ascii=False)
    assert "用户已确认" not in decided
    assert "已确认完整表达式" not in decided
    assert "已确认只支持字面量" not in decided
    again = asyncio.run(
        runner.run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="planning",
            input_data={"research_artifact_id": artifact.id},
            idempotency_key=planning_key(loaded),
        )
    )
    assert again.agent_run_id == result.agent_run_id
    assert model.calls == 1
    with session_scope(ctx.sessions) as session:
        usage = session.scalars(select(ModelUsageRow).where(ModelUsageRow.workflow_id == workflow_id)).all()
        tasks = session.scalar(select(func.count()).select_from(TaskRow))
        executions = session.scalar(select(func.count()).select_from(ExecutionRow))
    assert usage[0].prompt_tokens == 11
    assert usage[0].agent_name == "planning"
    assert tasks == 2
    assert executions == 0

    changed = _report(["另一个尚未回答的问题"])
    _changed_run, changed_artifact = _seed(ctx, project, workflow_id, changed)
    with session_scope(ctx.sessions) as session:
        changed_input = load_planning_input(session, workflow_id, changed_artifact.id)
    assert planning_key(changed_input) != planning_key(loaded)
    versioned = planning_key(loaded, agent_version="2")
    assert versioned != planning_key(loaded)


def test_planning_agent_normalizes_sloppy_model_fields(settings) -> None:
    ctx, project, _requirement, workflow_id = _workflow(settings)
    _run, artifact = _seed(ctx, project, workflow_id)
    sloppy = _draft()
    sloppy["tasks"][0]["estimated_complexity"] = "low"
    sloppy["tasks"][1]["task_type"] = "coding"
    sloppy["clarification_items"] = [{"question": "是否需要支持括号？", "status": "answered"}]
    result = asyncio.run(
        _runner(ctx, ScriptModel([sloppy])).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="planning",
            input_data={"research_artifact_id": artifact.id},
            idempotency_key="sloppy",
        )
    )
    assert result.status == AgentRunStatus.COMPLETED
    assert result.output["tasks"][0]["estimated_complexity"] == "small"
    assert all(item["status"] == "unresolved" for item in result.output["clarification_items"])


def test_truncated_plan_json_is_closed(settings) -> None:
    ctx, project, _requirement, workflow_id = _workflow(settings)
    _run, artifact = _seed(ctx, project, workflow_id)
    model = ScriptModel([json.dumps(_draft())[:-1]])
    result = asyncio.run(
        _runner(ctx, model).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="planning",
            input_data={"research_artifact_id": artifact.id},
            idempotency_key="truncated",
        )
    )
    assert model.calls == 1
    assert result.status == AgentRunStatus.COMPLETED


def test_planning_agent_does_not_persist_invalid_model_output(settings) -> None:
    ctx, project, _requirement, workflow_id = _workflow(settings)
    _run, artifact = _seed(ctx, project, workflow_id)
    broken = ScriptModel(["not-json", "still-bad", "{}"])
    failed = asyncio.run(
        _runner(ctx, broken).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="planning",
            input_data={"research_artifact_id": artifact.id},
            idempotency_key="bad-json",
        )
    )
    assert failed.status == AgentRunStatus.FAILED
    assert broken.calls == 3
    reused = asyncio.run(
        _runner(ctx, broken).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="planning",
            input_data={"research_artifact_id": artifact.id},
            idempotency_key="bad-json",
        )
    )
    assert reused.status == AgentRunStatus.FAILED
    assert broken.calls == 3

    incomplete = ScriptModel([{"planning_summary": "only a summary"}, {"planning_summary": "again"}, {"planning_summary": "third"}])
    incomplete_result = asyncio.run(
        _runner(ctx, incomplete).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="planning",
            input_data={"research_artifact_id": artifact.id},
            idempotency_key="incomplete",
        )
    )
    assert incomplete_result.status == AgentRunStatus.FAILED
    assert incomplete.calls == 3

    timed = ScriptModel([TimeoutError("slow")])
    timed_result = asyncio.run(
        _runner(ctx, timed).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="planning",
            input_data={"research_artifact_id": artifact.id},
            idempotency_key="timeout",
        )
    )
    assert timed_result.status == AgentRunStatus.TIMEOUT

    exploded = ScriptModel([RuntimeError("boom")])
    exploded_result = asyncio.run(
        _runner(ctx, exploded).run(
            workflow_id=workflow_id,
            project_id=project.id,
            agent_name="planning",
            input_data={"research_artifact_id": artifact.id},
            idempotency_key="explode",
        )
    )
    assert exploded_result.status == AgentRunStatus.FAILED
    assert exploded_result.error_message == "boom"
    with session_scope(ctx.sessions) as session:
        assert session.scalar(select(func.count()).select_from(TaskRow)) == 0
        assert session.scalar(select(func.count()).select_from(ExecutionRow)) == 0


def test_planning_workflow_checkpoint_api_and_repeat(settings, tmp_path: Path) -> None:
    ctx, project, _requirement, workflow_id = _workflow(settings, status="running")
    _run, artifact = _seed(ctx, project, workflow_id)
    model = ScriptModel([_draft(), _draft()])
    runner = _runner(ctx, model)
    connection = sqlite3.connect(tmp_path / "planning.sqlite", check_same_thread=False)
    saver = SqliteSaver(connection)
    saver.setup()
    with session_scope(ctx.sessions) as session:
        loaded = load_planning_input(session, workflow_id, artifact.id)
    config = {"configurable": {"thread_id": f"{workflow_id}:planning"}}
    token = bind_planning_runner(runner)
    try:
        paused = build_planning_graph(saver, interrupt_before=["planning"])
        paused.invoke(
            {
                "workflow_id": workflow_id,
                "project_id": project.id,
                "research_artifact_id": artifact.id,
                "planning_key": planning_key(loaded),
                "agent_run_ids": [],
                "artifact_ids": [],
            },
            config,
        )
        assert paused.get_state(config).next == ("planning",)
        assert model.calls == 0
        final = build_planning_graph(saver).invoke(None, config)
    finally:
        reset_planning_runner(token)
    assert model.calls == 1
    assert final["plan"]["validation_result"]["valid"] is True
    with session_scope(ctx.sessions) as session:
        update = get_workflow(session, workflow_id)
        assert update.status == "running"
        assert active_workflow_id(session, project.id) == workflow_id

    service = PlanningWorkflowService(ctx, checkpointer=MemorySaver(), complete_fn=model)
    started = service.start(workflow_id, artifact.id)
    assert started["status"] == "planned"
    assert model.calls == 1
    with session_scope(ctx.sessions) as session:
        assert get_workflow(session, workflow_id).status == "planned"
        assert active_workflow_id(session, project.id) is None
        task_count = session.scalar(select(func.count()).select_from(TaskRow))
    assert task_count == 2
    repeated = service.start(workflow_id, artifact.id)
    assert repeated["plan_id"] == started["plan_id"]
    assert model.calls == 1
    with session_scope(ctx.sessions) as session:
        assert session.scalar(select(func.count()).select_from(TaskRow)) == 2
        assert session.scalar(select(func.count()).select_from(DevelopmentPlanRow)) == 1

    missing = PlanningWorkflowService(ctx, checkpointer=MemorySaver(), complete_fn=model)
    with pytest.raises(PlanningInputError):
        missing.start(workflow_id, "missing-artifact")
    assert model.calls == 1

    failing = ScriptModel([RuntimeError("model down")])
    _failed_run, failed_artifact = _seed(ctx, project, workflow_id, _report(["另一个问题"]))
    with session_scope(ctx.sessions) as session:
        from ase.store import update_workflow

        update_workflow(session, workflow_id, "researched")
    with pytest.raises(Exception):
        PlanningWorkflowService(ctx, checkpointer=MemorySaver(), complete_fn=failing).start(workflow_id, failed_artifact.id)
    with session_scope(ctx.sessions) as session:
        assert get_workflow(session, workflow_id).status == "researched"
    cancelled = WorkflowService(ctx)
    snapshot = cancelled.cancel(workflow_id)
    assert snapshot["status"] == "researched"

    app = create_app(settings, ctx)
    app.state.planning = PlanningWorkflowService(ctx, checkpointer=MemorySaver(), complete_fn=ScriptModel([_draft()]))
    client = TestClient(app)
    response = client.post(f"/api/v1/workflows/{workflow_id}/planning", json={"research_artifact_id": artifact.id})
    assert response.status_code == 200
    assert "api_key" not in response.text
    plan_response = client.get(f"/api/v1/workflows/{workflow_id}/plan")
    tasks_response = client.get(f"/api/v1/workflows/{workflow_id}/tasks")
    assert plan_response.status_code == 200
    assert tasks_response.status_code == 200
    body = tasks_response.json()["data"]
    assert body["blocked_task_ids"]
    assert body["clarification_items"]
    assert body["dependencies"]
    assert all(item["status"] == "unresolved" for item in body["clarification_items"])


def test_validation_result_schema_requires_counts() -> None:
    with pytest.raises(ValidationError):
        PlanValidationResult.model_validate({"valid": True})
