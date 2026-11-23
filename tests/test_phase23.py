import json

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import func, select

from ase.app import build_context, create_app
from ase.db.orm import ExecutionRow, TaskRow
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.planning.review_service import PlanReviewError, PlanReviewService, plan_hash
from ase.planning.schemas import Plan
from ase.planning.service import PlanningWorkflowService
from ase.store import get_artifact, get_workflow, list_tasks_for_plan
from tests.test_phase22 import ScriptModel, _draft, _report, _seed, _workflow


def _service(ctx, model: ScriptModel) -> PlanReviewService:
    planning = PlanningWorkflowService(ctx, checkpointer=MemorySaver(), complete_fn=model)
    return PlanReviewService(planning)


def _planned(settings, questions: list[str]):
    ctx, project, _requirement, workflow_id = _workflow(settings, status="researched")
    _run, artifact = _seed(ctx, project, workflow_id, _report(questions))
    model = ScriptModel([_draft(), _draft(), _draft(), _draft()])
    service = _service(ctx, model)
    service.planning.start(workflow_id, artifact.id)
    return ctx, service, model, workflow_id, artifact


def test_answers_versions_and_approval_do_not_start_coding(settings) -> None:
    questions = [
        "科学计数法只支持字面量，还是完整表达式？",
        "非法输入应抛出 ValueError 还是返回错误值？",
    ]
    ctx, service, model, workflow_id, _artifact = _planned(settings, questions)
    versions = service.list_versions(workflow_id)
    assert versions[0]["plan_version"] == 1
    with session_scope(ctx.sessions) as session:
        original = get_artifact(session, versions[0]["plan_artifact_id"]).body
    items = service.list_clarifications(workflow_id)
    assert [item["state"] for item in items] == ["unresolved", "unresolved"]
    first, second = items
    saved = service.answer(workflow_id, first["clarification_id"], "完整数学表达式")
    again = service.answer(workflow_id, first["clarification_id"], "完整数学表达式")
    assert again["answer_revision"] == saved["answer_revision"]
    changed = service.answer(workflow_id, first["clarification_id"], "完整数学表达式，包含运算符和括号")
    history = service.history(workflow_id, first["clarification_id"])
    assert [item["answer_revision"] for item in history] == [1, 2]
    assert history[0]["answer"] == "完整数学表达式"
    assert service.history(workflow_id, second["clarification_id"]) == []
    with pytest.raises(PlanReviewError):
        service.answer(workflow_id, first["clarification_id"], "  ")
    with pytest.raises(PlanReviewError):
        service.answer(workflow_id, "missing", "完整数学表达式")
    deferred = service.defer(workflow_id, second["clarification_id"], "稍后再定非法输入")
    assert deferred["state"] == "deferred"
    blocked = service.blocked_tasks(workflow_id)
    assert any(item["agent_id"] == "implementation" for item in blocked)
    with session_scope(ctx.sessions) as session:
        assert get_artifact(session, versions[0]["plan_artifact_id"]).body == original
    revised = service.revise(workflow_id)
    assert revised["plan_version"] == 2
    assert revised["parent_plan_artifact_id"] == versions[0]["plan_artifact_id"]
    assert revised["source_research_artifact_id"] == versions[0]["source_research_artifact_id"]
    assert model.calls == 2
    repeated = service.revise(workflow_id)
    assert repeated["plan_version"] == 2
    assert model.calls == 2
    with session_scope(ctx.sessions) as session:
        assert get_artifact(session, versions[0]["plan_artifact_id"]).body == original
        version_one_tasks = list_tasks_for_plan(session, versions[0]["plan_id"])
    assert any(item.status.value == "blocked" and item.agent_id == "implementation" for item in version_one_tasks)
    with pytest.raises(WorkflowConflict):
        service.decide(workflow_id, "approved", "", revised["plan_artifact_id"], revised["plan_hash"])
    service.answer(workflow_id, second["clarification_id"], "非法输入是抛出 ValueError、返回错误值")
    third = service.revise(workflow_id)
    assert third["plan_version"] == 3
    approved = service.decide(workflow_id, "approved", "范围已经确认", third["plan_artifact_id"], third["plan_hash"])
    assert approved["status"] == "approved"
    assert approved["actor"] == "api-key"
    assert approved["plan_version"] == 3
    assert approved["plan_hash"] == third["plan_hash"]
    same = service.decide(workflow_id, "approved", "范围已经确认", third["plan_artifact_id"], third["plan_hash"])
    assert same["approval_id"] == approved["approval_id"]
    with pytest.raises(WorkflowConflict):
        service.decide(workflow_id, "rejected", "推翻", third["plan_artifact_id"], third["plan_hash"])
    with session_scope(ctx.sessions) as session:
        current_tasks = list_tasks_for_plan(session, third["plan_id"])
        old_tasks = list_tasks_for_plan(session, versions[0]["plan_id"])
        executions = session.scalar(select(func.count()).select_from(ExecutionRow))
        status = get_workflow(session, workflow_id).status
    assert any(item.status.value == "ready" and item.agent_id == "implementation" for item in current_tasks)
    assert any(item.status.value == "blocked" and item.agent_id == "implementation" for item in old_tasks)
    assert executions == 0
    assert status == "planned"
    edited = service.edit(workflow_id, {"tasks": [{"task_id": current_tasks[0].id, "title": "记录已经确认的范围"}]}, "用户改了标题")
    assert edited["plan_version"] == 4
    assert edited["plan_artifact_id"] != third["plan_artifact_id"]
    compared = service.diff(workflow_id, 3, 4)
    assert compared["changed_tasks"] or compared["added_tasks"] or compared["removed_tasks"]
    with pytest.raises(WorkflowConflict):
        service.decide(workflow_id, "approved", "", edited["plan_artifact_id"], "wrong-hash")
    requested = service.decide(
        workflow_id,
        "changes_requested",
        "再缩短标题",
        edited["plan_artifact_id"],
        edited["plan_hash"],
        [current_tasks[0].id],
    )
    assert requested["status"] == "changes_requested"
    with pytest.raises(WorkflowConflict):
        service.decide(workflow_id, "approved", "ok", edited["plan_artifact_id"], edited["plan_hash"])
    with session_scope(ctx.sessions) as session:
        stored = Plan.model_validate(json.loads(get_artifact(session, versions[0]["plan_artifact_id"]).body)["content"])
    assert plan_hash(stored) == versions[0]["plan_hash"]


def test_revision_keeps_dependencies_of_retained_tasks(settings) -> None:
    question = "科学计数法只支持字面量，还是完整表达式？"
    ctx, service, model, workflow_id, _artifact = _planned(settings, [question])
    parent = service.current_version(workflow_id)
    with session_scope(ctx.sessions) as session:
        parent_plan = Plan.model_validate(json.loads(get_artifact(session, parent["plan_artifact_id"]).body)["content"])
    parent_impl = next(task for task in parent_plan.tasks if task.task_type.value == "implementation")
    parent_docs = next(task for task in parent_plan.tasks if task.task_id in parent_impl.dependencies)
    replacement = _draft()
    replacement["tasks"] = [
        {
            **replacement["tasks"][0],
            "task_id": "note",
            "title": "Record the confirmed notation",
            "description": "Record the user decision without deleting the earlier tasks.",
        }
    ]
    model.responses = [replacement]
    item = service.list_clarifications(workflow_id)[0]
    service.answer(workflow_id, item["clarification_id"], "完整数学表达式")
    revised = service.revise(workflow_id)
    with session_scope(ctx.sessions) as session:
        plan = Plan.model_validate(json.loads(get_artifact(session, revised["plan_artifact_id"]).body)["content"])
        old_rows = list_tasks_for_plan(session, parent["plan_id"])
    retained = {task.title: task for task in plan.tasks}
    assert parent_impl.title in retained
    assert retained[parent_impl.title].dependencies == [retained[parent_docs.title].task_id]
    assert {row.id for row in old_rows} == {task.task_id for task in parent_plan.tasks}


def test_plan_review_api_does_not_resume_coding(settings) -> None:
    ctx, service, _model, workflow_id, _artifact = _planned(settings, ["科学计数法只支持字面量，还是完整表达式？"])
    app = create_app(settings, ctx)
    app.state.plan_review = service
    client = TestClient(app)
    headers = {"X-ASE-API-KEY": "test-key"}
    listed = client.get(f"/api/v1/workflows/{workflow_id}/clarifications")
    assert listed.status_code == 200
    clarification_id = listed.json()["data"][0]["clarification_id"]
    denied = client.post(f"/api/v1/workflows/{workflow_id}/clarifications/{clarification_id}/answer", json={"answer": "完整数学表达式"})
    assert denied.status_code == 401
    accepted = client.post(
        f"/api/v1/workflows/{workflow_id}/clarifications/{clarification_id}/answer",
        headers=headers,
        json={"answer": "完整数学表达式"},
    )
    assert accepted.status_code == 200
    revised = client.post(f"/api/v1/workflows/{workflow_id}/plan-revision", headers=headers)
    assert revised.status_code == 200
    version = revised.json()["data"]
    decision = client.post(
        f"/api/v1/workflows/{workflow_id}/plan-approval",
        headers=headers,
        json={
            "decision": "approved",
            "comment": "确认",
            "plan_artifact_id": version["plan_artifact_id"],
            "plan_hash": version["plan_hash"],
        },
    )
    assert decision.status_code == 200
    approval_id = decision.json()["data"]["approval_id"]
    legacy = client.post(f"/api/v1/approvals/{approval_id}/approve", headers=headers, json={"comment": "resume"})
    assert legacy.status_code == 409
    with session_scope(ctx.sessions) as session:
        assert get_workflow(session, workflow_id).status == "planned"
        assert session.scalar(select(func.count()).select_from(ExecutionRow)) == 0
        assert session.scalar(select(func.count()).select_from(TaskRow).where(TaskRow.agent_id == "coding")) == 0
