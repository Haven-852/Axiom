"""Plan versions, clarification history, and approval. This does not resume the coding workflow."""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select

from ase.contracts.domain import Approval, ApprovalStatus, TaskStatus
from ase.db.orm import WorkflowRow
from ase.db.session import session_scope
from ase.errors import WorkflowConflict
from ase.planning.dag_validator import finalize_plan
from ase.planning.research_input import load_planning_input
from ase.planning.schemas import Plan, PlanTask
from ase.planning.service import PlanningWorkflowService
from ase.planning.task_builder import persist_plan
from ase.store import (
    audit,
    find_artifact_by_uri,
    get_approval_by_subject,
    get_artifact,
    get_workflow,
    list_artifacts,
    list_tasks_for_plan,
    save_approval,
    save_artifact,
    set_task_status,
)
from ase.contracts.domain import Artifact

ACTOR = "api-key"


class PlanReviewError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class PlanReviewService:
    def __init__(self, planning: PlanningWorkflowService) -> None:
        self.planning = planning
        self.ctx = planning.ctx

    def list_versions(self, workflow_id: str) -> list[dict]:
        with session_scope(self.ctx.sessions) as session:
            self._require_workflow(session, workflow_id)
            self._ensure_initial_version(session, workflow_id)
            return self._versions(session, workflow_id)

    def get_version(self, workflow_id: str, plan_version: int) -> dict:
        versions = self.list_versions(workflow_id)
        found = next((item for item in versions if item["plan_version"] == plan_version), None)
        if found is None:
            raise PlanReviewError("plan_version_not_found", "plan version does not exist", 404)
        return found

    def current_version(self, workflow_id: str) -> dict:
        versions = self.list_versions(workflow_id)
        if not versions:
            raise PlanReviewError("plan_not_found", "workflow has no plan", 404)
        return versions[-1]

    def diff(self, workflow_id: str, left_version: int, right_version: int) -> dict:
        left = self._plan_for_version(workflow_id, left_version)
        right = self._plan_for_version(workflow_id, right_version)
        return _diff(left, right)

    def list_clarifications(self, workflow_id: str) -> list[dict]:
        current = self.current_version(workflow_id)
        plan = self._load_plan(current["plan_artifact_id"])
        with session_scope(self.ctx.sessions) as session:
            records = self._decision_records(session, workflow_id)
        latest = _latest_by_question(records)
        return [_clarification_view(item, latest.get(item.question)) for item in plan.clarification_items]

    def history(self, workflow_id: str, clarification_id: str) -> list[dict]:
        question = self._question_for(workflow_id, clarification_id)
        with session_scope(self.ctx.sessions) as session:
            records = [item for item in self._decision_records(session, workflow_id) if item["clarification_id"] == clarification_id or item["question"] == question]
        if not records and question is None:
            raise PlanReviewError("clarification_not_found", "clarification does not exist", 404)
        return records

    def answer(self, workflow_id: str, clarification_id: str, answer: str) -> dict:
        text = answer.strip()
        if not text:
            raise PlanReviewError("empty_answer", "answer is empty")
        return self._record_decision(workflow_id, clarification_id, "clarification_answer", text, "answered")

    def defer(self, workflow_id: str, clarification_id: str, reason: str) -> dict:
        text = reason.strip()
        if not text:
            raise PlanReviewError("empty_reason", "defer reason is empty")
        return self._record_decision(workflow_id, clarification_id, "clarification_deferral", text, "deferred")

    def blocked_tasks(self, workflow_id: str) -> list[dict]:
        current = self.current_version(workflow_id)
        with session_scope(self.ctx.sessions) as session:
            tasks = list_tasks_for_plan(session, current["plan_id"])
        return [task.model_dump(mode="json") for task in tasks if task.status == TaskStatus.BLOCKED]

    def resolution_status(self, workflow_id: str) -> dict:
        items = self.list_clarifications(workflow_id)
        blocking = [item for item in items if item["blocking"] and item["state"] != "answered"]
        return {
            "clarifications": items,
            "blocking_open": [item["clarification_id"] for item in blocking],
            "answered_count": sum(item["state"] == "answered" for item in items),
            "unresolved_count": sum(item["state"] == "unresolved" for item in items),
        }

    def revise(self, workflow_id: str) -> dict:
        current = self.current_version(workflow_id)
        plan = self._load_plan(current["plan_artifact_id"])
        status = self.resolution_status(workflow_id)
        decisions = [
            {"clarification_id": item["clarification_id"], "question": item["question"], "answer": item["answer"]}
            for item in status["clarifications"]
            if item["state"] == "answered" and item["answer"]
        ]
        if not decisions:
            raise PlanReviewError("no_answers", "there is no user answer to revise from")
        deferred = [item["question"] for item in status["clarifications"] if item["state"] == "deferred"]
        fingerprint = _fingerprint(
            {"decisions": sorted((item["question"], item["answer"]) for item in decisions), "deferred": sorted(deferred)}
        )
        if current.get("origin") == "planning" and current.get("clarification_revision") == fingerprint:
            return current
        uri = f"plan-version://{workflow_id}/{current['plan_artifact_id']}/{fingerprint}"
        with session_scope(self.ctx.sessions) as session:
            existing = find_artifact_by_uri(session, workflow_id, uri)
            if existing is not None:
                return json.loads(existing.body)
        research_artifact_id = current["source_research_artifact_id"]
        started = self.planning.start(
            workflow_id,
            research_artifact_id,
            decisions=decisions,
            parent_plan_artifact_id=current["plan_artifact_id"],
            parent_clarifications=[item.model_dump(mode="json") for item in plan.clarification_items],
            parent_tasks=[_task_snapshot(task) for task in plan.tasks],
            clarification_revision=fingerprint,
            deferred_questions=deferred,
            thread_suffix=fingerprint[:12],
        )
        return self._register_child(workflow_id, current, started, fingerprint, uri, "planning")

    def edit(self, workflow_id: str, changes: dict, reason: str) -> dict:
        if not str(reason or "").strip():
            raise PlanReviewError("empty_reason", "edit reason is empty")
        current = self.current_version(workflow_id)
        fingerprint = _fingerprint({"parent": current["plan_artifact_id"], "changes": changes, "reason": reason.strip()})
        uri = f"plan-version://{workflow_id}/{fingerprint}"
        with session_scope(self.ctx.sessions) as session:
            existing = find_artifact_by_uri(session, workflow_id, uri)
            if existing is not None:
                return json.loads(existing.body)
            self._lock(session, workflow_id)
            loaded = load_planning_input(session, workflow_id, current["source_research_artifact_id"])
            requirement_id = loaded.requirement_id
        plan = self._load_plan(current["plan_artifact_id"])
        revised = _apply_edit(plan, changes, fingerprint)
        resolutions = {item["question"]: item["answer"] for item in self.resolution_status(workflow_id)["clarifications"] if item["state"] == "answered"}
        deferred = {item.question for item in revised.clarification_items if item.status.value == "deferred"}
        sealed, result = finalize_plan(revised, loaded.research_report, resolutions or None, deferred or None)
        if not result.valid:
            raise PlanReviewError("plan_invalid", "; ".join(result.errors))
        persist_plan(self.ctx.sessions, requirement_id, sealed, hold_for_approval=True)
        artifact_id = self._save_plan_artifact(workflow_id, loaded, sealed, f"plan-edit://{sealed.plan_id}", "manual-edit")
        started = {"plan_artifact_id": artifact_id, "agent_run_id": "manual-edit", "plan_id": sealed.plan_id}
        record = self._register_child(workflow_id, current, started, fingerprint, uri, "edit")
        with session_scope(self.ctx.sessions) as session:
            audit(session, ACTOR, "plan_edit", sealed.plan_id, {"reason": reason.strip(), "plan_version": record["plan_version"]})
        return record

    def decide(
        self,
        workflow_id: str,
        decision: str,
        comment: str,
        plan_artifact_id: str,
        plan_hash_value: str,
        affected_task_ids: list[str] | None = None,
    ) -> dict:
        if decision not in {ApprovalStatus.APPROVED.value, ApprovalStatus.REJECTED.value, ApprovalStatus.CHANGES_REQUESTED.value}:
            raise PlanReviewError("decision_invalid", "decision is not supported")
        if decision != ApprovalStatus.APPROVED.value and not comment.strip():
            raise PlanReviewError("comment_required", "reject and changes_requested require a comment")
        with session_scope(self.ctx.sessions) as session:
            self._lock(session, workflow_id)
            versions = self._versions(session, workflow_id) or self._ensure_initial_version(session, workflow_id)
            version = next((item for item in versions if item["plan_artifact_id"] == plan_artifact_id), None)
            if version is None:
                raise PlanReviewError("plan_not_found", "plan artifact is not a known version", 404)
            if version["plan_version"] != versions[-1]["plan_version"]:
                raise WorkflowConflict("only the current plan version can be reviewed")
            artifact = get_artifact(session, plan_artifact_id)
            if artifact is None:
                raise PlanReviewError("plan_not_found", "plan artifact does not exist", 404)
            plan = Plan.model_validate(json.loads(artifact.body)["content"])
            actual_hash = plan_hash(plan)
            if plan_hash_value != actual_hash:
                raise WorkflowConflict("plan hash does not match the stored plan")
            loaded = load_planning_input(session, workflow_id, version["source_research_artifact_id"])
            resolutions = _answers_for_plan(plan, self._decision_records(session, workflow_id))
            deferred = {item.question for item in plan.clarification_items if item.status.value == "deferred"}
            _sealed, result = finalize_plan(plan, loaded.research_report, resolutions or None, deferred or None)
            if not result.valid:
                raise PlanReviewError("plan_invalid", "; ".join(result.errors))
            if decision == ApprovalStatus.APPROVED.value:
                blockers = _open_blockers(plan, self._decision_records(session, workflow_id))
                if blockers:
                    raise WorkflowConflict("unresolved blocking clarifications: " + ", ".join(blockers))
            subject = f"plan-review:{plan_artifact_id}:{actual_hash}"
            existing = get_approval_by_subject(session, workflow_id, subject)
            if existing is not None:
                if existing.status.value == decision:
                    return _approval_view(existing, version, actual_hash)
                raise WorkflowConflict("this plan version already has a different decision")
            approval = Approval(
                project_id=loaded.project_id,
                workflow_id=workflow_id,
                subject=subject,
                status=ApprovalStatus(decision),
                comment=json.dumps(
                    {
                        "actor": ACTOR,
                        "comment": comment,
                        "plan_id": plan.plan_id,
                        "plan_version": version["plan_version"],
                        "plan_artifact_id": plan_artifact_id,
                        "plan_hash": actual_hash,
                        "affected_task_ids": list(affected_task_ids or []),
                    },
                    ensure_ascii=False,
                ),
            )
            save_approval(session, approval)
            if decision == ApprovalStatus.APPROVED.value:
                for task_id in result.topological_order:
                    if task_id in set(plan.execution_order):
                        set_task_status(session, task_id, TaskStatus.READY.value)
            audit(session, ACTOR, f"plan_{decision}", approval.id, {"plan_artifact_id": plan_artifact_id, "plan_hash": actual_hash})
            return _approval_view(approval, version, actual_hash)

    def list_decisions(self, workflow_id: str) -> list[dict]:
        with session_scope(self.ctx.sessions) as session:
            self._require_workflow(session, workflow_id)
            from ase.db.orm import ApprovalRow

            rows = session.scalars(select(ApprovalRow).where(ApprovalRow.workflow_id == workflow_id)).all()
        return [
            {
                "approval_id": row.id,
                "status": row.status,
                "subject": row.subject,
                "comment": row.comment,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
            if str(row.subject).startswith("plan-review:")
        ]

    def _record_decision(self, workflow_id: str, clarification_id: str, kind: str, text: str, state: str) -> dict:
        question = self._question_for(workflow_id, clarification_id)
        if question is None:
            raise PlanReviewError("clarification_not_found", "clarification does not exist", 404)
        current = self.current_version(workflow_id)
        plan = self._load_plan(current["plan_artifact_id"])
        item = next(entry for entry in plan.clarification_items if entry.clarification_id == clarification_id or entry.question == question)
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        uri = f"{kind}://{current['plan_id']}/{item.clarification_id}/{digest}"
        with session_scope(self.ctx.sessions) as session:
            self._lock(session, workflow_id)
            existing = find_artifact_by_uri(session, workflow_id, uri)
            if existing is not None:
                return json.loads(existing.body)
            prior = [record for record in self._decision_records(session, workflow_id) if record["clarification_id"] == item.clarification_id]
            body = {
                "clarification_id": item.clarification_id,
                "question": item.question,
                "answer": text,
                "answered_by": ACTOR,
                "answered_at": datetime.now(timezone.utc).isoformat(),
                "plan_id": current["plan_id"],
                "plan_version": current["plan_version"],
                "source": "user",
                "source_reference": item.source_reference or item.source,
                "answer_revision": len(prior) + 1,
                "state": state,
                "affected_task_ids": list(item.affected_task_ids),
            }
            workflow = get_workflow(session, workflow_id)
            save_artifact(
                session,
                Artifact(
                    project_id=workflow.project_id,
                    workflow_id=workflow_id,
                    kind=kind,
                    uri=uri,
                    body=json.dumps(body, ensure_ascii=False),
                ),
            )
            audit(session, ACTOR, state, item.clarification_id, {"plan_version": current["plan_version"], "answer_revision": body["answer_revision"]})
            return body

    def _register_child(self, workflow_id: str, parent: dict, started: dict, fingerprint: str, uri: str, origin: str) -> dict:
        with session_scope(self.ctx.sessions) as session:
            self._lock(session, workflow_id)
            existing = find_artifact_by_uri(session, workflow_id, uri)
            if existing is not None:
                return json.loads(existing.body)
            versions = self._versions(session, workflow_id)
            artifact = get_artifact(session, started["plan_artifact_id"])
            envelope = json.loads(artifact.body)
            plan = Plan.model_validate(envelope["content"])
            record = {
                "schema_version": "1.0",
                "plan_id": plan.plan_id,
                "plan_version": len(versions) + 1,
                "parent_plan_artifact_id": parent["plan_artifact_id"],
                "source_research_artifact_id": parent["source_research_artifact_id"],
                "source_research_agent_run_id": parent["source_research_agent_run_id"],
                "planning_agent_run_id": started.get("agent_run_id") or origin,
                "plan_artifact_id": started["plan_artifact_id"],
                "plan_hash": plan_hash(plan),
                "clarification_revision": fingerprint,
                "origin": origin,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            workflow = get_workflow(session, workflow_id)
            save_artifact(
                session,
                Artifact(
                    project_id=workflow.project_id,
                    workflow_id=workflow_id,
                    kind="plan_version",
                    uri=uri,
                    body=json.dumps(record, ensure_ascii=False),
                ),
            )
            return record

    def _ensure_initial_version(self, session, workflow_id: str) -> list[dict]:
        versions = self._versions(session, workflow_id)
        if versions:
            return versions
        plans = list_artifacts(session, workflow_id, kind="plan")
        if not plans:
            return []
        artifact = plans[0]
        envelope = json.loads(artifact.body)
        plan = Plan.model_validate(envelope["content"])
        record = {
            "schema_version": "1.0",
            "plan_id": plan.plan_id,
            "plan_version": 1,
            "parent_plan_artifact_id": None,
            "source_research_artifact_id": envelope.get("research_artifact_id") or plan.source_research_artifact_id,
            "source_research_agent_run_id": envelope.get("research_agent_run_id") or plan.source_research_agent_run_id,
            "planning_agent_run_id": envelope.get("planning_agent_run_id"),
            "plan_artifact_id": artifact.id,
            "plan_hash": plan_hash(plan),
            "clarification_revision": "",
            "origin": "initial",
            "created_at": artifact.created_at.isoformat(),
        }
        workflow = get_workflow(session, workflow_id)
        save_artifact(
            session,
            Artifact(
                project_id=workflow.project_id,
                workflow_id=workflow_id,
                kind="plan_version",
                uri=f"plan-version://{workflow_id}/1",
                body=json.dumps(record, ensure_ascii=False),
            ),
        )
        return [record]

    def _versions(self, session, workflow_id: str) -> list[dict]:
        rows = list_artifacts(session, workflow_id, kind="plan_version")
        versions = [json.loads(row.body) | {"version_artifact_id": row.id} for row in rows]
        return sorted(versions, key=lambda item: item["plan_version"])

    def _decision_records(self, session, workflow_id: str) -> list[dict]:
        rows = [
            row
            for row in list_artifacts(session, workflow_id)
            if row.kind in {"clarification_answer", "clarification_deferral"}
        ]
        return [json.loads(row.body) | {"record_id": row.id, "kind": row.kind} for row in rows]

    def _question_for(self, workflow_id: str, clarification_id: str) -> str | None:
        current = self.current_version(workflow_id)
        plan = self._load_plan(current["plan_artifact_id"])
        for item in plan.clarification_items:
            if item.clarification_id == clarification_id:
                return item.question
        with session_scope(self.ctx.sessions) as session:
            for version in self._versions(session, workflow_id):
                if version["plan_artifact_id"] == current["plan_artifact_id"]:
                    continue
                try:
                    older = self._load_plan(version["plan_artifact_id"])
                except PlanReviewError:
                    continue
                for item in older.clarification_items:
                    if item.clarification_id == clarification_id:
                        return item.question
        return None

    def _load_plan(self, artifact_id: str) -> Plan:
        with session_scope(self.ctx.sessions) as session:
            artifact = get_artifact(session, artifact_id)
        if artifact is None:
            raise PlanReviewError("plan_not_found", "plan artifact does not exist", 404)
        return Plan.model_validate(json.loads(artifact.body)["content"])

    def _plan_for_version(self, workflow_id: str, plan_version: int) -> Plan:
        version = self.get_version(workflow_id, plan_version)
        return self._load_plan(version["plan_artifact_id"])

    def _save_plan_artifact(self, workflow_id: str, loaded, plan: Plan, uri: str, agent_run_id: str) -> str:
        body = json.dumps(
            {
                "schema_version": "1.0",
                "planning_agent_run_id": agent_run_id,
                "workflow_id": workflow_id,
                "research_artifact_id": loaded.research_artifact_id,
                "research_agent_run_id": loaded.research_agent_run_id,
                "task_ids": [task.task_id for task in plan.tasks],
                "blocked_task_ids": [task.task_id for task in plan.tasks if task.task_id not in set(plan.execution_order)],
                "content": plan.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
        with session_scope(self.ctx.sessions) as session:
            existing = find_artifact_by_uri(session, workflow_id, uri)
            if existing is not None:
                return existing.id
            artifact = Artifact(
                project_id=loaded.project_id,
                workflow_id=workflow_id,
                kind="plan",
                uri=uri,
                body=body,
            )
            save_artifact(session, artifact)
            return artifact.id

    def _require_workflow(self, session, workflow_id: str) -> None:
        if get_workflow(session, workflow_id) is None:
            raise PlanReviewError("workflow_not_found", "workflow does not exist", 404)

    def _lock(self, session, workflow_id: str) -> None:
        row = session.scalar(select(WorkflowRow).where(WorkflowRow.id == workflow_id).with_for_update())
        if row is None:
            raise PlanReviewError("workflow_not_found", "workflow does not exist", 404)


def plan_hash(plan: Plan) -> str:
    return hashlib.sha256(json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _task_snapshot(task: PlanTask) -> dict:
    return task.model_dump(mode="json")


def _latest_by_question(records: list[dict]) -> dict[str, dict]:
    ordered = sorted(records, key=lambda item: (item.get("answered_at") or "", item.get("answer_revision") or 0))
    return {item["question"]: item for item in ordered}


def _clarification_view(item, record: dict | None) -> dict:
    state = item.status.value
    answer = item.answer
    if record is not None:
        state = record["state"]
        answer = record["answer"] if record["state"] == "answered" else ""
    return {
        "clarification_id": item.clarification_id,
        "question": item.question,
        "context": item.context,
        "impact": item.impact,
        "blocking": item.blocking if state != "answered" else False,
        "affected_task_ids": list(item.affected_task_ids),
        "source": item.source,
        "source_reference": item.source_reference,
        "state": state,
        "answer": answer,
    }


def _answers_for_plan(plan: Plan, records: list[dict]) -> dict[str, str]:
    latest = _latest_by_question([item for item in records if item["state"] == "answered"])
    return {
        item.question: item.answer
        for item in plan.clarification_items
        if item.status.value == "answered" and item.question in latest and latest[item.question]["answer"] == item.answer
    }


def _open_blockers(plan: Plan, records: list[dict]) -> list[str]:
    latest = _latest_by_question(records)
    blockers = []
    for item in plan.clarification_items:
        record = latest.get(item.question)
        if item.status.value == "answered" and record is not None and record["state"] == "answered" and record["answer"] == item.answer:
            continue
        if item.status.value == "answered":
            blockers.append(item.clarification_id)
        elif item.blocking:
            blockers.append(item.clarification_id)
    return blockers


def _approval_view(approval: Approval, version: dict, actual_hash: str) -> dict:
    return {
        "approval_id": approval.id,
        "status": approval.status.value,
        "workflow_id": approval.workflow_id,
        "plan_id": version["plan_id"],
        "plan_version": version["plan_version"],
        "plan_artifact_id": version["plan_artifact_id"],
        "plan_hash": actual_hash,
        "actor": ACTOR,
        "comment": approval.comment,
        "created_at": approval.created_at.isoformat(),
    }


def _apply_edit(plan: Plan, changes: dict, fingerprint: str) -> Plan:
    removed = set(changes.get("delete_task_ids") or [])
    updates = {item["task_id"]: item for item in changes.get("tasks") or []}
    plan_id = hashlib.sha256(f"{plan.plan_id}:{fingerprint}".encode()).hexdigest()
    plan_id = str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, plan_id))
    id_map = {task.task_id: str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, f"{plan_id}:{task.task_id}")) for task in plan.tasks if task.task_id not in removed}
    tasks = []
    for task in plan.tasks:
        if task.task_id in removed:
            continue
        patch = updates.get(task.task_id) or {}
        data = task.model_dump(mode="json")
        for field in ("title", "description", "priority", "acceptance_criteria", "related_files"):
            if field in patch:
                data[field] = patch[field]
        data["task_id"] = id_map[task.task_id]
        data["dependencies"] = [id_map[item] for item in patch.get("dependencies", task.dependencies) if item in id_map]
        tasks.append(PlanTask.model_validate(data))
    for index, raw in enumerate(changes.get("add") or []):
        raw = dict(raw)
        raw["task_id"] = str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, f"{plan_id}:add:{index}"))
        raw["dependencies"] = [id_map[item] for item in raw.get("dependencies") or [] if item in id_map]
        tasks.append(PlanTask.model_validate(raw))
    if not tasks:
        raise PlanReviewError("plan_invalid", "a plan must keep at least one task")
    return plan.model_copy(update={"plan_id": plan_id, "tasks": tasks, "execution_order": [], "planning_summary": plan.planning_summary})


def _diff(left: Plan, right: Plan) -> dict:
    left_titles = {task.title: task for task in left.tasks}
    right_titles = {task.title: task for task in right.tasks}
    changed = []
    for title in sorted(set(left_titles) & set(right_titles)):
        fields = []
        for field in ("description", "priority", "task_type", "acceptance_criteria", "related_files"):
            if getattr(left_titles[title], field) != getattr(right_titles[title], field):
                fields.append(field)
        left_deps = _dependency_titles(left, left_titles[title])
        right_deps = _dependency_titles(right, right_titles[title])
        if left_deps != right_deps:
            fields.append("dependencies")
        if fields:
            changed.append({"title": title, "fields": fields})
    left_questions = {item.question: item.status.value for item in left.clarification_items}
    right_questions = {item.question: item.status.value for item in right.clarification_items}
    return {
        "added_tasks": sorted(set(right_titles) - set(left_titles)),
        "removed_tasks": sorted(set(left_titles) - set(right_titles)),
        "changed_tasks": changed,
        "added_clarifications": sorted(set(right_questions) - set(left_questions)),
        "removed_clarifications": sorted(set(left_questions) - set(right_questions)),
        "clarification_status_changes": [
            {"question": question, "from": left_questions[question], "to": right_questions[question]}
            for question in sorted(set(left_questions) & set(right_questions))
            if left_questions[question] != right_questions[question]
        ],
    }


def _dependency_titles(plan: Plan, task: PlanTask) -> list[str]:
    names = {item.task_id: item.title for item in plan.tasks}
    return sorted(names.get(item, item) for item in task.dependencies)
