import json
import subprocess

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ase.app import build_context, create_app
from ase.contracts.domain import Approval, ApprovalStatus, DevelopmentPlan, EvaluationReport, Project, Requirement, Task, TaskStatus
from ase.contracts.domain import TestResult as SavedTestResult
from ase.contracts.domain import new_id
from ase.db.orm import ExecutionEventRow, ExecutionRow
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.execution.eligibility import EligibilityError
from ase.execution.task_scheduler import select_next
from ase.planning.review_service import plan_hash
from ase.planning.schemas import Plan, PlanTask, PlanValidationResult
from ase.store import get_task, save_approval, save_artifact, save_evaluation, save_plan, save_project, save_requirement, save_test_result, save_workflow
from ase.contracts.domain import Artifact
from ase.agents.coding_agent import CodingAgent, coding_definition
from ase.agents.registry import AgentRegistry
from ase.agents.runner import AgentRunner
from ase.execution.coding_service import CodingTaskService
from ase.workflow.graph import build_graph


def _plan(task_id: str, docs_id: str | None = None, blocker: str | None = None) -> Plan:
    tasks = [
        PlanTask(
            task_id=docs_id or task_id,
            title="Record the decision",
            description="Record the confirmed scope.",
            task_type="documentation",
            priority="medium",
            related_files=["README.md"],
            acceptance_criteria=["The decision is recorded."],
            estimated_complexity="small",
            source_findings=["CF-001"],
        )
    ]
    if docs_id:
        tasks.append(
            PlanTask(
                task_id=task_id,
                title="Implement the change",
                description="Change only the listed file.",
                task_type="implementation",
                priority="high",
                dependencies=[docs_id],
                related_files=["calculator.py"],
                acceptance_criteria=["The expression matches the decision."],
                estimated_complexity="medium",
                source_findings=["CF-001"],
                blocking_clarification_ids=[blocker] if blocker else [],
            )
        )
    clarifications = []
    if blocker:
        from ase.planning.schemas import ClarificationItem

        clarifications.append(
            ClarificationItem(
                clarification_id=blocker,
                question="What should the output look like?",
                context="The output format is still open.",
                impact="Implementation cannot choose a format.",
                blocking=True,
                status="unresolved",
                source="planning",
                affected_task_ids=[task_id],
            )
        )
    order = [item.task_id for item in tasks]
    return Plan(
        schema_version="1.0",
        plan_id=task_id[:8] + "plan",
        source_research_artifact_id="research-artifact",
        source_research_agent_run_id="research-run",
        requirement_summary="Add one confirmed behavior.",
        planning_summary="One documentation task and an optional implementation.",
        tasks=tasks,
        clarification_items=clarifications,
        execution_order=[item for item in order if not blocker or item != task_id],
        validation_result=PlanValidationResult(
            valid=True,
            structurally_valid=True,
            task_count=len(tasks),
            blocked_task_count=1 if blocker else 0,
            executable_task_count=len(tasks) - (1 if blocker else 0),
            topological_order=order,
        ),
    )


def _approved(settings, *, approved=True, blocker: str | None = None, with_dependency=False):
    ctx = build_context(settings)
    project = Project(name=f"coding-gate-{new_id()[:8]}", repo_path="E:/unused", base_ref="HEAD")
    requirement = Requirement(project_id=project.id, title="gate", statement="confirmed change")
    workflow_id = new_id()
    docs_id = new_id() if with_dependency or blocker else None
    task_id = new_id() if docs_id else new_id()
    plan = _plan(task_id, docs_id, blocker)
    if not docs_id:
        task_id = plan.tasks[0].task_id
    digest = plan_hash(plan)
    artifact_id = new_id()
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
        save_workflow(session, workflow_id, project.id, requirement.id, "planned")
        save_plan(
            session,
            DevelopmentPlan(
                id=plan.plan_id,
                requirement_id=requirement.id,
                summary=plan.planning_summary,
                tasks=[
                    Task(id=item.task_id, plan_id=plan.plan_id, title=item.title, instruction=item.description, status=TaskStatus.READY, agent_id=item.task_type.value)
                    for item in plan.tasks
                ],
            ),
        )
        save_artifact(
            session,
            Artifact(id=artifact_id, project_id=project.id, workflow_id=workflow_id, kind="plan", uri=f"plan://{artifact_id}", body=json.dumps({"content": plan.model_dump(mode="json")})),
        )
        save_artifact(
            session,
            Artifact(
                id=new_id(),
                project_id=project.id,
                workflow_id=workflow_id,
                kind="plan_version",
                uri=f"plan-version://{workflow_id}/1",
                body=json.dumps(
                    {
                        "plan_id": plan.plan_id,
                        "plan_version": 1,
                        "plan_artifact_id": artifact_id,
                        "plan_hash": digest,
                        "source_research_artifact_id": plan.source_research_artifact_id,
                    }
                ),
            ),
        )
        if approved:
            save_approval(
                session,
                Approval(
                    project_id=project.id,
                    workflow_id=workflow_id,
                    subject=f"plan-review:{artifact_id}:{digest}",
                    status=ApprovalStatus.APPROVED,
                    comment=json.dumps(
                        {
                            "actor": "api-key",
                            "plan_id": plan.plan_id,
                            "plan_version": 1,
                            "plan_artifact_id": artifact_id,
                            "plan_hash": digest,
                        }
                    ),
                ),
            )
    registry = AgentRegistry()
    registry.register(coding_definition(), lambda: CodingAgent(ctx.coding, ctx.sessions))
    service = CodingTaskService(ctx, AgentRunner(registry, ctx.sessions, settings))
    return ctx, service, workflow_id, task_id, plan


def _workspace(settings):
    workspace = settings.workspace_root / "job"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=workspace, check=True, capture_output=True)
    (workspace / "README.md").write_text("scope\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=ase@example.com", "-c", "user.name=ASE", "commit", "-m", "init"], cwd=workspace, check=True, capture_output=True)
    return workspace


def test_unapproved_plan_creates_no_execution(settings) -> None:
    ctx, service, workflow_id, task_id, _plan_model = _approved(settings, approved=False)
    try:
        service.eligibility(workflow_id, task_id)
        raise AssertionError("expected rejection")
    except EligibilityError as exc:
        assert exc.code == "plan_not_approved"
    with session_scope(ctx.sessions) as session:
        assert session.scalar(select(func.count()).select_from(ExecutionRow)) == 0


def test_blocker_dependency_and_hash_stop_before_execution(settings) -> None:
    ctx, service, workflow_id, task_id, plan = _approved(settings, blocker="c-open", with_dependency=True)
    try:
        service.eligibility(workflow_id, task_id)
        raise AssertionError("expected blocker")
    except EligibilityError as exc:
        assert "c-open" in str(exc)
    with session_scope(ctx.sessions) as session:
        assert session.scalar(select(func.count()).select_from(ExecutionRow)) == 0
    _ctx, ready_service, ready_workflow, ready_task, ready_plan = _approved(settings, with_dependency=True)
    try:
        ready_service.eligibility(ready_workflow, ready_task)
        raise AssertionError("expected open dependency")
    except EligibilityError as exc:
        assert exc.code == "dependency_open"
    status = {item.task_id: "ready" for item in ready_plan.tasks}
    first = next(item.task_id for item in ready_plan.tasks if not item.dependencies)
    assert select_next(ready_plan, status, set()) == first


def test_approved_task_runs_once_and_completion_needs_evidence(settings) -> None:
    ctx, service, workflow_id, task_id, _plan_model = _approved(settings)
    workspace = _workspace(settings)
    started = service.start_task(workflow_id, task_id, str(workspace))
    assert started["test_status"] == "unverified"
    with session_scope(ctx.sessions) as session:
        task = get_task(session, task_id)
        executions = session.scalar(select(func.count()).select_from(ExecutionRow))
    assert task.status == TaskStatus.IN_PROGRESS
    assert executions == 1
    with session_scope(ctx.sessions) as session:
        row = session.get(ExecutionRow, started["execution_id"])
        event_count = session.scalar(
            select(func.count()).select_from(ExecutionEventRow).where(ExecutionEventRow.execution_id == started["execution_id"])
        )
    assert row.conversation_id
    assert event_count >= 1
    again = service.start_task(workflow_id, task_id, str(workspace))
    with session_scope(ctx.sessions) as session:
        assert session.scalar(select(func.count()).select_from(ExecutionRow)) == 1
    assert again["execution_id"] == started["execution_id"]
    try:
        service.complete_task(workflow_id, task_id, started["execution_id"])
        raise AssertionError("expected unverified")
    except EligibilityError as exc:
        assert exc.code == "task_unverified"
    with session_scope(ctx.sessions) as session:
        save_test_result(session, SavedTestResult(execution_id=started["execution_id"], passed=True, command=["pytest"], exit_code=0, output="1 passed"))
        save_evaluation(session, EvaluationReport(workflow_id=workflow_id, passed=True, summary="matches the criterion"))
    gate = service.complete_task(workflow_id, task_id, started["execution_id"])
    assert gate["test_status"] == "automated_verified"
    with session_scope(ctx.sessions) as session:
        assert get_task(session, task_id).status == TaskStatus.COMPLETED
    graph = build_graph(None)
    assert "diagnose" in graph.nodes and "repair" in graph.nodes


def test_legacy_resume_does_not_code_a_plan_managed_workflow(settings) -> None:
    from ase.workflow.service import WorkflowService

    ctx, _service, workflow_id, _task_id, _plan_model = _approved(settings)
    try:
        WorkflowService(ctx).resume(workflow_id, "approved", "legacy approval")
        raise AssertionError("expected plan-managed workflow to stay out of the legacy graph")
    except WorkflowConflict as exc:
        assert "plan-managed" in str(exc)


def test_coding_api_requires_approval(settings) -> None:
    ctx, service, workflow_id, task_id, _plan_model = _approved(settings, approved=False)
    app = create_app(settings, ctx)
    app.state.coding_tasks = service
    client = TestClient(app)
    denied = client.post(
        f"/api/v1/workflows/{workflow_id}/coding-tasks/{task_id}",
        json={"workspace_path": str(settings.workspace_root / "job")},
    )
    assert denied.status_code == 401
    rejected = client.post(
        f"/api/v1/workflows/{workflow_id}/coding-tasks/{task_id}",
        headers={"X-ASE-API-KEY": "test-key"},
        json={"workspace_path": str(settings.workspace_root / "missing")},
    )
    assert rejected.status_code == 409
    with session_scope(ctx.sessions) as session:
        assert session.scalar(select(func.count()).select_from(ExecutionRow)) == 0
