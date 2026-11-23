"""Load the Phase 2.1 research artifact envelope. Do not guess another shape."""

import hashlib
import json
from dataclasses import dataclass

from pydantic import ValidationError

from ase.agents.research.schemas import ResearchReport
from ase.store import get_agent_run, get_artifact, get_workflow


class PlanningInputError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PlanningInput:
    workflow_id: str
    project_id: str
    requirement_id: str
    research_artifact_id: str
    research_agent_run_id: str
    research_report: ResearchReport
    repository_revision: str | None
    research_report_hash: str


def load_planning_input(session, workflow_id: str, research_artifact_id: str) -> PlanningInput:
    workflow = get_workflow(session, workflow_id)
    if workflow is None:
        raise PlanningInputError("workflow_not_found", "workflow does not exist")
    artifact = get_artifact(session, research_artifact_id)
    if artifact is None:
        raise PlanningInputError("artifact_not_found", "research artifact does not exist")
    if artifact.workflow_id != workflow_id or artifact.kind != "research_report":
        raise PlanningInputError("artifact_workflow_mismatch", "artifact does not belong to this workflow")
    try:
        body = json.loads(artifact.body)
    except json.JSONDecodeError as exc:
        raise PlanningInputError("artifact_json_invalid", "research artifact body is not JSON") from exc
    if not isinstance(body, dict) or "content" not in body or "agent_run_id" not in body:
        raise PlanningInputError("artifact_envelope_invalid", "research artifact is missing the Phase 2.1 envelope")
    if body.get("workflow_id") != workflow_id:
        raise PlanningInputError("artifact_workflow_mismatch", "artifact envelope workflow does not match")
    try:
        report = ResearchReport.model_validate(body["content"])
    except ValidationError as exc:
        raise PlanningInputError("research_report_invalid", str(exc)[:500]) from exc
    run = get_agent_run(session, str(body["agent_run_id"]))
    if run is None or run.workflow_id != workflow_id or run.agent_name != "research":
        raise PlanningInputError("research_run_mismatch", "research agent run does not match the artifact")
    canonical = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    revision = body.get("revision")
    return PlanningInput(
        workflow_id=workflow_id,
        project_id=workflow.project_id,
        requirement_id=workflow.requirement_id,
        research_artifact_id=artifact.id,
        research_agent_run_id=run.id,
        research_report=report,
        repository_revision=revision if isinstance(revision, str) else None,
        research_report_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def planning_key(
    item: PlanningInput,
    *,
    agent_version: str = "1",
    clarification_revision: str = "",
    parent_plan_artifact_id: str = "",
    user_change_hash: str = "",
) -> str:
    payload = {
        "workflow_id": item.workflow_id,
        "research_artifact_id": item.research_artifact_id,
        "research_report_hash": item.research_report_hash,
        "agent_name": "planning",
        "agent_version": agent_version,
        "format_repairs": 2,
        "prompt_version": "3",
    }
    if clarification_revision:
        payload["clarification_revision"] = clarification_revision
    if parent_plan_artifact_id:
        payload["parent_plan_artifact_id"] = parent_plan_artifact_id
    if user_change_hash:
        payload["user_change_hash"] = user_change_hash
    blob = json.dumps(payload, sort_keys=True)
    digest = hashlib.sha256(blob.encode()).hexdigest()[:32]
    return f"{item.workflow_id}:planning:{digest}"
