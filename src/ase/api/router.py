from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from ase.contracts.domain import Project, Requirement
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.planning.research_input import PlanningInputError
from ase.store import (
    audit,
    get_artifact,
    get_execution,
    get_project,
    get_requirement,
    get_task,
    list_approvals,
    list_artifacts,
    list_dependencies_for_plan,
    list_model_usage,
    list_projects,
    list_tasks_for_plan,
    save_project,
    save_requirement,
)
from ase.workflow.service import WorkflowService


api_router = APIRouter()


class ProjectIn(BaseModel):
    name: str
    repo_path: str
    base_ref: str = "HEAD"
    description: str = ""


class RequirementIn(BaseModel):
    project_id: str
    title: str
    statement: str


class WorkflowIn(BaseModel):
    project_id: str
    requirement_id: str


class PlanningIn(BaseModel):
    research_artifact_id: str


class AnswerIn(BaseModel):
    answer: str


class DeferIn(BaseModel):
    reason: str


class EditIn(BaseModel):
    reason: str
    changes: dict = Field(default_factory=dict)


class CodingStartIn(BaseModel):
    workspace_path: str
    repository_revision: str = ""


class PlanDecisionIn(BaseModel):
    decision: str
    comment: str = ""
    plan_artifact_id: str
    plan_hash: str
    affected_task_ids: list[str] = Field(default_factory=list)


class ResearchIn(BaseModel):
    project_id: str
    requirement: str
    research_options: dict[str, object] = Field(default_factory=dict)


class DecisionIn(BaseModel):
    comment: str = ""


def _ctx(request: Request):
    return request.app.state.ctx


def _workflows(request: Request) -> WorkflowService:
    return request.app.state.workflows


def _ok(data):
    return {"ok": True, "data": data}


def _require_key(request: Request, x_ase_api_key: str = Header(default="")) -> str:
    expected = request.app.state.ctx.settings.api_key
    if x_ase_api_key != expected:
        raise HTTPException(status_code=401, detail="approval key rejected")
    return x_ase_api_key


@api_router.post("/projects")
def create_project(body: ProjectIn, request: Request):
    project = Project(name=body.name, repo_path=body.repo_path, base_ref=body.base_ref, description=body.description)
    with session_scope(_ctx(request).sessions) as session:
        save_project(session, project)
    return _ok(project.model_dump(mode="json"))


@api_router.get("/projects")
def get_projects(request: Request):
    with session_scope(_ctx(request).sessions) as session:
        items = list_projects(session)
    return _ok([item.model_dump(mode="json") for item in items])


@api_router.post("/requirements")
def create_requirement(body: RequirementIn, request: Request):
    with session_scope(_ctx(request).sessions) as session:
        if get_project(session, body.project_id) is None:
            raise HTTPException(status_code=404, detail="project not found")
        item = Requirement(project_id=body.project_id, title=body.title, statement=body.statement)
        save_requirement(session, item)
    return _ok(item.model_dump(mode="json"))


@api_router.get("/requirements/{requirement_id}")
def read_requirement(requirement_id: str, request: Request):
    with session_scope(_ctx(request).sessions) as session:
        item = get_requirement(session, requirement_id)
    if item is None:
        raise HTTPException(status_code=404, detail="requirement not found")
    return _ok(item.model_dump(mode="json"))


@api_router.post("/workflows")
def create_workflow(body: WorkflowIn, request: Request):
    with session_scope(_ctx(request).sessions) as session:
        requirement = get_requirement(session, body.requirement_id)
        project = get_project(session, body.project_id)
    if requirement is None or project is None or requirement.project_id != project.id:
        raise HTTPException(status_code=404, detail="project or requirement not found")
    try:
        snapshot = _workflows(request).start(project.id, requirement.id, requirement.statement)
    except WorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _ok(snapshot)


@api_router.get("/workflows/{workflow_id}")
def read_workflow(workflow_id: str, request: Request):
    try:
        return _ok(_workflows(request).snapshot(workflow_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found") from None


@api_router.post("/workflows/{workflow_id}/cancel")
def cancel_workflow(workflow_id: str, request: Request, _: str = Depends(_require_key)):
    try:
        return _ok(_workflows(request).cancel(workflow_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found") from None


@api_router.post("/workflows/{workflow_id}/continue")
def continue_workflow(workflow_id: str, request: Request, _: str = Depends(_require_key)):
    try:
        return _ok(_workflows(request).continue_unfinished(workflow_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found") from None


@api_router.get("/usage")
def read_usage(
    request: Request,
    project_id: str | None = None,
    workflow_id: str | None = None,
    task_id: str | None = None,
    model: str | None = None,
):
    with session_scope(_ctx(request).sessions) as session:
        items = list_model_usage(
            session,
            project_id=project_id,
            workflow_id=workflow_id,
            task_id=task_id,
            model=model,
        )
    return _ok([item.model_dump(mode="json") for item in items])


@api_router.get("/workflows/{workflow_id}/events")
def read_events(workflow_id: str, request: Request):
    snapshot = _workflows(request).snapshot(workflow_id)
    return _ok(snapshot["events"])


@api_router.get("/approvals")
def read_approvals(request: Request, _: str = Depends(_require_key)):
    with session_scope(_ctx(request).sessions) as session:
        items = list_approvals(session)
    return _ok([item.model_dump(mode="json") for item in items])


@api_router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, body: DecisionIn, request: Request, _: str = Depends(_require_key)):
    return _decide(approval_id, "approved", body.comment, request)


@api_router.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, body: DecisionIn, request: Request, _: str = Depends(_require_key)):
    return _decide(approval_id, "rejected", body.comment, request)


@api_router.get("/tasks/{task_id}")
def read_task(task_id: str, request: Request):
    with session_scope(_ctx(request).sessions) as session:
        item = get_task(session, task_id)
    if item is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _ok(item.model_dump(mode="json"))


@api_router.get("/executions/{execution_id}")
def read_execution(execution_id: str, request: Request):
    with session_scope(_ctx(request).sessions) as session:
        item = get_execution(session, execution_id)
    if item is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return _ok(item.model_dump(mode="json"))


@api_router.get("/artifacts/{artifact_id}")
def read_artifact(artifact_id: str, request: Request):
    with session_scope(_ctx(request).sessions) as session:
        item = get_artifact(session, artifact_id)
    if item is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return _ok(item.model_dump(mode="json"))


@api_router.post("/workflows/{workflow_id}/planning")
def start_planning(workflow_id: str, body: PlanningIn, request: Request):
    try:
        result = request.app.state.planning.start(workflow_id, body.research_artifact_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found") from None
    except PlanningInputError as exc:
        status = 404 if exc.code in {"artifact_not_found", "workflow_not_found"} else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except WorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _ok(result)


@api_router.get("/workflows/{workflow_id}/plan")
def read_plan(workflow_id: str, request: Request):
    payload = _plan_body(workflow_id, request)
    if payload is None:
        raise HTTPException(status_code=404, detail="plan not found")
    artifact, body = payload
    return _ok({"artifact_id": artifact.id, "blocked_task_ids": body.get("blocked_task_ids", []), "plan": body.get("content")})


@api_router.get("/workflows/{workflow_id}/tasks")
def read_planned_tasks(workflow_id: str, request: Request):
    payload = _plan_body(workflow_id, request)
    if payload is None:
        raise HTTPException(status_code=404, detail="plan not found")
    _artifact, body = payload
    content = body.get("content") or {}
    plan_id = content.get("plan_id")
    with session_scope(_ctx(request).sessions) as session:
        tasks = list_tasks_for_plan(session, plan_id) if plan_id else []
        dependencies = list_dependencies_for_plan(session, plan_id) if plan_id else []
    return _ok(
        {
            "plan_id": plan_id,
            "tasks": [item.model_dump(mode="json") for item in tasks],
            "dependencies": [item.model_dump(mode="json") for item in dependencies],
            "blocked_task_ids": body.get("blocked_task_ids", []),
            "clarification_items": content.get("clarification_items", []),
        }
    )


def _plan_body(workflow_id: str, request: Request):
    import json

    with session_scope(_ctx(request).sessions) as session:
        artifacts = list_artifacts(session, workflow_id, kind="plan")
    if not artifacts:
        return None
    artifact = artifacts[-1]
    return artifact, json.loads(artifact.body)


def _review(request: Request):
    return request.app.state.plan_review


def _review_call(action):
    from ase.planning.review_service import PlanReviewError

    try:
        return _ok(action())
    except PlanReviewError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except WorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found") from None


@api_router.get("/workflows/{workflow_id}/plan-versions")
def list_plan_versions(workflow_id: str, request: Request):
    return _review_call(lambda: _review(request).list_versions(workflow_id))


@api_router.get("/workflows/{workflow_id}/plan-versions/{plan_version}")
def read_plan_version(workflow_id: str, plan_version: int, request: Request):
    return _review_call(lambda: _review(request).get_version(workflow_id, plan_version))


@api_router.get("/workflows/{workflow_id}/plan-diff")
def read_plan_diff(workflow_id: str, left: int, right: int, request: Request):
    return _review_call(lambda: _review(request).diff(workflow_id, left, right))


@api_router.get("/workflows/{workflow_id}/clarifications")
def list_clarifications(workflow_id: str, request: Request):
    return _review_call(lambda: _review(request).list_clarifications(workflow_id))


@api_router.get("/workflows/{workflow_id}/clarifications/{clarification_id}/history")
def clarification_history(workflow_id: str, clarification_id: str, request: Request):
    return _review_call(lambda: _review(request).history(workflow_id, clarification_id))


@api_router.post("/workflows/{workflow_id}/clarifications/{clarification_id}/answer")
def answer_clarification(workflow_id: str, clarification_id: str, body: AnswerIn, request: Request, _: str = Depends(_require_key)):
    return _review_call(lambda: _review(request).answer(workflow_id, clarification_id, body.answer))


@api_router.post("/workflows/{workflow_id}/clarifications/{clarification_id}/defer")
def defer_clarification(workflow_id: str, clarification_id: str, body: DeferIn, request: Request, _: str = Depends(_require_key)):
    return _review_call(lambda: _review(request).defer(workflow_id, clarification_id, body.reason))


@api_router.get("/workflows/{workflow_id}/blocked-tasks")
def blocked_tasks(workflow_id: str, request: Request):
    return _review_call(lambda: _review(request).blocked_tasks(workflow_id))


@api_router.post("/workflows/{workflow_id}/plan-revision")
def revise_plan(workflow_id: str, request: Request, _: str = Depends(_require_key)):
    return _review_call(lambda: _review(request).revise(workflow_id))


@api_router.post("/workflows/{workflow_id}/plan-edits")
def edit_plan(workflow_id: str, body: EditIn, request: Request, _: str = Depends(_require_key)):
    return _review_call(lambda: _review(request).edit(workflow_id, body.changes, body.reason))


@api_router.post("/workflows/{workflow_id}/plan-approval")
def decide_plan(workflow_id: str, body: PlanDecisionIn, request: Request, _: str = Depends(_require_key)):
    return _review_call(
        lambda: _review(request).decide(
            workflow_id,
            body.decision,
            body.comment,
            body.plan_artifact_id,
            body.plan_hash,
            body.affected_task_ids,
        )
    )


@api_router.get("/workflows/{workflow_id}/plan-approvals")
def list_plan_approvals(workflow_id: str, request: Request, _: str = Depends(_require_key)):
    return _review_call(lambda: _review(request).list_decisions(workflow_id))


@api_router.get("/workflows/{workflow_id}/coding-eligibility/{task_id}")
def coding_eligibility(workflow_id: str, task_id: str, request: Request):
    try:
        return _ok(request.app.state.coding_tasks.eligibility(workflow_id, task_id))
    except WorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.post("/workflows/{workflow_id}/coding-tasks/{task_id}")
def start_coding_task(workflow_id: str, task_id: str, body: CodingStartIn, request: Request, _: str = Depends(_require_key)):
    try:
        return _ok(request.app.state.coding_tasks.start_task(workflow_id, task_id, body.workspace_path, body.repository_revision))
    except WorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@api_router.post("/workflows/{workflow_id}/coding-runs")
def start_coding_run(workflow_id: str, body: CodingStartIn, request: Request, _: str = Depends(_require_key)):
    try:
        return _ok(request.app.state.coding_tasks.start_next(workflow_id, body.workspace_path, body.repository_revision))
    except WorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.post("/research-workflows")
def start_research(body: ResearchIn, request: Request):
    try:
        result = request.app.state.research.start(body.project_id, body.requirement, body.research_options)
    except KeyError:
        raise HTTPException(status_code=404, detail="project not found") from None
    except WorkflowConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _ok(result)


@api_router.get("/research-workflows/{workflow_id}")
def read_research(workflow_id: str, request: Request):
    try:
        return _ok(request.app.state.research.status(workflow_id))
    except KeyError:
        raise HTTPException(status_code=404, detail="workflow not found") from None


def _decide(approval_id: str, decision: str, comment: str, request: Request):
    ctx = _ctx(request)
    with session_scope(ctx.sessions) as session:
        from ase.store import get_approval as load

        item = load(session, approval_id)
        if item is None:
            raise HTTPException(status_code=404, detail="approval not found")
        if str(item.subject).startswith("plan-review:"):
            raise HTTPException(status_code=409, detail="plan approval does not resume the coding workflow")
        audit(session, "api-key", decision, approval_id, {"comment": comment, "workflow_id": item.workflow_id})
        workflow_id = item.workflow_id
    snapshot = _workflows(request).resume(workflow_id, decision, comment)
    return _ok(snapshot)
