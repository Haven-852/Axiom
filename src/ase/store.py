from sqlalchemy import select
from sqlalchemy.orm import Session

from ase.contracts.domain import (
    AgentRun,
    AgentRunStatus,
    Approval,
    Artifact,
    DevelopmentPlan,
    EvaluationReport,
    Execution,
    ExecutionEvent,
    ModelUsage,
    OperationRecord,
    Project,
    Requirement,
    RequirementAnalysis,
    Task,
    TaskDependency,
    TestResult,
    utcnow,
    new_id,
)
from ase.db.orm import (
    AgentRegistryRow,
    AgentRunExecutionRow,
    AgentRunRow,
    RepositoryLeaseRow,
    ApprovalRow,
    ArtifactRow,
    AuditLogRow,
    DevelopmentPlanRow,
    EvaluationReportRow,
    ExecutionEventRow,
    ExecutionOperationRow,
    ExecutionRow,
    ModelUsageRow,
    ProjectRow,
    RequirementAnalysisRow,
    RequirementRow,
    TaskDependencyRow,
    TaskRow,
    TestResultRow,
    WorkflowCheckpointRow,
    WorkflowRow,
)
from ase.execution import assert_transition, canonical_status


def save_project(session: Session, project: Project) -> Project:
    session.add(
        ProjectRow(
            id=project.id,
            name=project.name,
            repo_path=project.repo_path,
            base_ref=project.base_ref,
            description=project.description,
            created_at=project.created_at,
            updated_at=project.updated_at,
        )
    )
    return project


def list_projects(session: Session) -> list[Project]:
    rows = session.scalars(select(ProjectRow).order_by(ProjectRow.created_at)).all()
    return [
        Project(
            id=row.id,
            name=row.name,
            repo_path=row.repo_path,
            base_ref=row.base_ref,
            description=row.description,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def get_project(session: Session, project_id: str) -> Project | None:
    row = session.get(ProjectRow, project_id)
    if row is None:
        return None
    return Project(
        id=row.id,
        name=row.name,
        repo_path=row.repo_path,
        base_ref=row.base_ref,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def save_requirement(session: Session, item: Requirement) -> Requirement:
    session.add(
        RequirementRow(
            id=item.id,
            project_id=item.project_id,
            title=item.title,
            statement=item.statement,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
    )
    return item


def get_requirement(session: Session, requirement_id: str) -> Requirement | None:
    row = session.get(RequirementRow, requirement_id)
    if row is None:
        return None
    return Requirement(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        statement=row.statement,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def save_analysis(session: Session, item: RequirementAnalysis) -> None:
    session.add(
        RequirementAnalysisRow(
            id=item.id,
            requirement_id=item.requirement_id,
            summary=item.summary,
            acceptance=item.acceptance,
            risks=item.risks,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
    )


def save_plan(session: Session, plan: DevelopmentPlan) -> None:
    session.add(
        DevelopmentPlanRow(
            id=plan.id,
            requirement_id=plan.requirement_id,
            summary=plan.summary,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )
    )
    for task in plan.tasks:
        session.add(
            TaskRow(
                id=task.id,
                plan_id=plan.id,
                title=task.title,
                instruction=task.instruction,
                status=task.status.value,
                agent_id=task.agent_id,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )
    session.flush()
    for edge in plan.dependencies:
        session.add(
            TaskDependencyRow(
                id=edge.id,
                plan_id=plan.id,
                predecessor_id=edge.predecessor_id,
                successor_id=edge.successor_id,
                created_at=edge.created_at,
                updated_at=edge.updated_at,
            )
        )


def list_tasks_for_plan(session: Session, plan_id: str) -> list[Task]:
    rows = session.scalars(select(TaskRow).where(TaskRow.plan_id == plan_id)).all()
    return [_task(row) for row in rows]


def list_dependencies_for_plan(session: Session, plan_id: str) -> list[TaskDependency]:
    rows = session.scalars(select(TaskDependencyRow).where(TaskDependencyRow.plan_id == plan_id)).all()
    return [
        TaskDependency(
            id=row.id,
            plan_id=row.plan_id,
            predecessor_id=row.predecessor_id,
            successor_id=row.successor_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def list_artifacts(session: Session, workflow_id: str, kind: str | None = None) -> list[Artifact]:
    query = select(ArtifactRow).where(ArtifactRow.workflow_id == workflow_id).order_by(ArtifactRow.created_at)
    if kind is not None:
        query = query.where(ArtifactRow.kind == kind)
    rows = session.scalars(query).all()
    return [_artifact(row) for row in rows]


def _task(row: TaskRow) -> Task:
    return Task(
        id=row.id,
        plan_id=row.plan_id,
        title=row.title,
        instruction=row.instruction,
        status=row.status,
        agent_id=row.agent_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def get_task(session: Session, task_id: str) -> Task | None:
    row = session.get(TaskRow, task_id)
    if row is None:
        return None
    return _task(row)


def save_workflow(session: Session, workflow_id: str, project_id: str, requirement_id: str, status: str) -> None:
    now = utcnow()
    session.add(
        WorkflowRow(
            id=workflow_id,
            project_id=project_id,
            requirement_id=requirement_id,
            status=status,
            thread_id=workflow_id,
            error=None,
            created_at=now,
            updated_at=now,
        )
    )


def update_workflow(session: Session, workflow_id: str, status: str, error: str | None = None) -> None:
    row = session.get(WorkflowRow, workflow_id)
    if row is None:
        raise KeyError(workflow_id)
    row.status = status
    row.error = error
    row.updated_at = utcnow()
    if status in {"delivered", "failed", "cancelled"}:
        release_workflow_leases(session, workflow_id)


def get_workflow(session: Session, workflow_id: str) -> WorkflowRow | None:
    return session.get(WorkflowRow, workflow_id)


def add_checkpoint_index(session: Session, workflow_id: str, node: str) -> None:
    session.add(
        WorkflowCheckpointRow(
            id=new_id(),
            workflow_id=workflow_id,
            thread_id=workflow_id,
            node=node,
            created_at=utcnow(),
        )
    )


def save_approval(session: Session, item: Approval) -> None:
    existing = session.scalar(
        select(ApprovalRow).where(ApprovalRow.workflow_id == item.workflow_id, ApprovalRow.subject == item.subject)
    )
    if existing is not None:
        return
    session.add(
        ApprovalRow(
            id=item.id,
            project_id=item.project_id,
            workflow_id=item.workflow_id,
            subject=item.subject,
            status=item.status.value,
            comment=item.comment,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
    )


def list_approvals(session: Session) -> list[Approval]:
    rows = session.scalars(select(ApprovalRow).order_by(ApprovalRow.created_at)).all()
    return [_approval(row) for row in rows]


def get_approval_by_subject(session: Session, workflow_id: str, subject: str) -> Approval | None:
    row = session.scalar(select(ApprovalRow).where(ApprovalRow.workflow_id == workflow_id, ApprovalRow.subject == subject))
    return None if row is None else _approval(row)


def set_task_status(session: Session, task_id: str, status: str) -> None:
    row = session.get(TaskRow, task_id)
    if row is None:
        raise KeyError(task_id)
    row.status = status
    row.updated_at = utcnow()


def get_approval(session: Session, approval_id: str) -> Approval | None:
    row = session.get(ApprovalRow, approval_id)
    return None if row is None else _approval(row)


def set_approval(session: Session, approval_id: str, status: str, comment: str) -> Approval:
    row = session.get(ApprovalRow, approval_id)
    if row is None:
        raise KeyError(approval_id)
    row.status = status
    row.comment = comment
    row.updated_at = utcnow()
    return _approval(row)


def save_execution(session: Session, item: Execution) -> None:
    session.add(
        ExecutionRow(
            id=item.id,
            project_id=item.project_id,
            workflow_id=item.workflow_id,
            task_id=item.task_id,
            agent_id=item.agent_id,
            status=item.status.value,
            session_id=item.session_id,
            workspace_id=item.workspace_id,
            conversation_id=item.conversation_id,
            container_id=item.container_id,
            operation_id=item.operation_id,
            attempt=item.attempt,
            error=item.error,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
    )


def update_execution(session: Session, item: Execution) -> None:
    row = session.get(ExecutionRow, item.id)
    if row is None:
        raise KeyError(item.id)
    if canonical_status(row.status) != canonical_status(item.status.value):
        assert_transition(row.status, item.status.value)
    row.status = canonical_status(item.status.value)
    row.session_id = item.session_id
    row.workspace_id = item.workspace_id
    row.conversation_id = item.conversation_id
    row.container_id = item.container_id
    row.operation_id = item.operation_id
    row.attempt = item.attempt
    row.error = item.error
    row.updated_at = utcnow()


def get_execution(session: Session, execution_id: str) -> Execution | None:
    row = session.get(ExecutionRow, execution_id)
    if row is None:
        return None
    return Execution(
        id=row.id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        task_id=row.task_id,
        agent_id=row.agent_id,
        status=row.status,
        session_id=row.session_id,
        workspace_id=row.workspace_id,
        conversation_id=row.conversation_id,
        container_id=row.container_id,
        operation_id=row.operation_id,
        attempt=row.attempt,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def save_event(session: Session, item: ExecutionEvent) -> None:
    session.add(
        ExecutionEventRow(
            id=item.id,
            project_id=item.project_id,
            workflow_id=item.workflow_id,
            task_id=item.task_id,
            execution_id=item.execution_id,
            event_type=item.event_type,
            source=item.source.value,
            payload=item.payload,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
    )


def list_events(session: Session, workflow_id: str) -> list[ExecutionEvent]:
    rows = session.scalars(
        select(ExecutionEventRow)
        .where(ExecutionEventRow.workflow_id == workflow_id)
        .order_by(ExecutionEventRow.created_at)
    ).all()
    return [
        ExecutionEvent(
            id=row.id,
            project_id=row.project_id,
            workflow_id=row.workflow_id,
            task_id=row.task_id,
            execution_id=row.execution_id,
            event_type=row.event_type,
            source=row.source,
            payload=row.payload,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def save_artifact(session: Session, item: Artifact) -> None:
    session.add(
        ArtifactRow(
            id=item.id,
            project_id=item.project_id,
            workflow_id=item.workflow_id,
            execution_id=item.execution_id,
            kind=item.kind,
            uri=item.uri,
            body=item.body,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
    )


def find_artifact_by_uri(session: Session, workflow_id: str, uri: str) -> Artifact | None:
    row = session.scalar(select(ArtifactRow).where(ArtifactRow.workflow_id == workflow_id, ArtifactRow.uri == uri))
    if row is None:
        return None
    return _artifact(row)


def get_artifact(session: Session, artifact_id: str) -> Artifact | None:
    row = session.get(ArtifactRow, artifact_id)
    if row is None:
        return None
    return _artifact(row)


def _artifact(row: ArtifactRow) -> Artifact:
    return Artifact(
        id=row.id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        execution_id=row.execution_id,
        kind=row.kind,
        uri=row.uri,
        body=row.body,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def save_test_result(session: Session, item: TestResult) -> None:
    session.add(
        TestResultRow(
            id=item.id,
            execution_id=item.execution_id,
            passed=item.passed,
            command=item.command,
            exit_code=item.exit_code,
            output=item.output,
            duration_seconds=item.duration_seconds,
            passed_count=item.passed_count,
            failed_count=item.failed_count,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
    )


def save_evaluation(session: Session, item: EvaluationReport) -> None:
    session.add(
        EvaluationReportRow(
            id=item.id,
            workflow_id=item.workflow_id,
            passed=item.passed,
            summary=item.summary,
            findings=item.findings,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
    )


def save_agent_spec(session: Session, agent_id: str, name: str, agent_type: str, description: str, spec: dict) -> None:
    now = utcnow()
    session.merge(
        AgentRegistryRow(
            agent_id=agent_id,
            name=name,
            agent_type=agent_type,
            description=description,
            spec=spec,
            created_at=now,
            updated_at=now,
        )
    )


def audit(session: Session, actor: str, action: str, subject_id: str, detail: dict) -> None:
    session.add(
        AuditLogRow(
            id=new_id(),
            actor=actor,
            action=action,
            subject_id=subject_id,
            detail=detail,
            created_at=utcnow(),
        )
    )


def active_workflow_id(session: Session, project_id: str) -> str | None:
    terminal = ("delivered", "failed", "cancelled", "researched", "planned")
    row = session.scalar(
        select(WorkflowRow).where(WorkflowRow.project_id == project_id, WorkflowRow.status.notin_(terminal))
    )
    return None if row is None else row.id


def claim_operation(session: Session, item: OperationRecord) -> OperationRecord:
    row = session.get(ExecutionOperationRow, item.id)
    if row is not None:
        return _operation(row)
    session.add(
        ExecutionOperationRow(
            id=item.id,
            project_id=item.project_id,
            workflow_id=item.workflow_id,
            task_id=item.task_id,
            kind=item.kind,
            attempt=item.attempt,
            status=item.status,
            execution_id=item.execution_id,
            conversation_id=item.conversation_id,
            container_id=item.container_id,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
    )
    session.flush()
    return item


def save_operation(session: Session, item: OperationRecord) -> None:
    row = session.get(ExecutionOperationRow, item.id)
    if row is None:
        claim_operation(session, item)
        return
    row.status = item.status
    row.execution_id = item.execution_id
    row.conversation_id = item.conversation_id
    row.container_id = item.container_id
    row.updated_at = utcnow()


def get_operation(session: Session, operation_id: str) -> OperationRecord | None:
    row = session.get(ExecutionOperationRow, operation_id)
    return None if row is None else _operation(row)


def save_model_usage(session: Session, item: ModelUsage) -> None:
    session.add(
        ModelUsageRow(
            id=item.id,
            project_id=item.project_id,
            workflow_id=item.workflow_id,
            task_id=item.task_id,
            execution_id=item.execution_id,
            provider=item.provider,
            model=item.model,
            prompt_tokens=item.prompt_tokens,
            completion_tokens=item.completion_tokens,
            total_tokens=item.total_tokens,
            latency_seconds=item.latency_seconds,
            estimated_cost=item.estimated_cost,
            error=item.error,
            agent_name=item.agent_name,
            agent_run_id=item.agent_run_id,
            created_at=item.created_at,
        )
    )


def list_model_usage(
    session: Session,
    *,
    project_id: str | None = None,
    workflow_id: str | None = None,
    task_id: str | None = None,
    model: str | None = None,
) -> list[ModelUsage]:
    query = select(ModelUsageRow).order_by(ModelUsageRow.created_at)
    if project_id:
        query = query.where(ModelUsageRow.project_id == project_id)
    if workflow_id:
        query = query.where(ModelUsageRow.workflow_id == workflow_id)
    if task_id:
        query = query.where(ModelUsageRow.task_id == task_id)
    if model:
        query = query.where(ModelUsageRow.model == model)
    return [_usage(row) for row in session.scalars(query).all()]


def _operation(row: ExecutionOperationRow) -> OperationRecord:
    return OperationRecord(
        id=row.id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        task_id=row.task_id,
        kind=row.kind,
        attempt=row.attempt,
        status=row.status,
        execution_id=row.execution_id,
        conversation_id=row.conversation_id,
        container_id=row.container_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _usage(row: ModelUsageRow) -> ModelUsage:
    return ModelUsage(
        id=row.id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        task_id=row.task_id,
        execution_id=row.execution_id,
        provider=row.provider,
        model=row.model,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.total_tokens,
        latency_seconds=row.latency_seconds,
        estimated_cost=row.estimated_cost,
        error=row.error,
        agent_name=row.agent_name,
        agent_run_id=row.agent_run_id,
        created_at=row.created_at,
        updated_at=row.created_at,
    )


def save_agent_run(session: Session, item: AgentRun) -> AgentRun:
    existing = None
    if item.idempotency_key:
        existing = session.scalar(select(AgentRunRow).where(AgentRunRow.idempotency_key == item.idempotency_key))
    if existing is not None:
        return _agent_run(existing, _execution_ids(session, existing.id))
    session.add(
        AgentRunRow(
            id=item.id,
            workflow_id=item.workflow_id,
            project_id=item.project_id,
            task_id=item.task_id,
            agent_name=item.agent_name,
            agent_version=item.agent_version,
            status=item.status.value,
            input_artifact_ids=list(item.input_artifact_ids),
            output_artifact_ids=list(item.output_artifact_ids),
            execution_ids=list(item.execution_ids),
            input_snapshot=dict(item.input_snapshot),
            output_snapshot=dict(item.output_snapshot),
            error_code=item.error_code,
            error_message=item.error_message,
            idempotency_key=item.idempotency_key,
            started_at=item.started_at,
            completed_at=item.completed_at,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
    )
    session.flush()
    return item


def update_agent_run(session: Session, item: AgentRun) -> None:
    from ase.agents.lifecycle import assert_agent_transition

    row = session.get(AgentRunRow, item.id)
    if row is None:
        raise KeyError(item.id)
    if row.status != item.status.value:
        assert_agent_transition(row.status, item.status.value)
    row.status = item.status.value
    row.output_artifact_ids = list(item.output_artifact_ids)
    row.execution_ids = list(item.execution_ids)
    row.output_snapshot = dict(item.output_snapshot)
    row.error_code = item.error_code
    row.error_message = item.error_message
    row.started_at = item.started_at
    row.completed_at = item.completed_at
    row.updated_at = utcnow()
    item.updated_at = row.updated_at


def get_agent_run(session: Session, agent_run_id: str) -> AgentRun | None:
    row = session.get(AgentRunRow, agent_run_id)
    if row is None:
        return None
    return _agent_run(row, _execution_ids(session, row.id))


def get_agent_run_by_key(session: Session, idempotency_key: str) -> AgentRun | None:
    row = session.scalar(select(AgentRunRow).where(AgentRunRow.idempotency_key == idempotency_key))
    if row is None:
        return None
    return _agent_run(row, _execution_ids(session, row.id))


def link_agent_execution(session: Session, agent_run_id: str, execution_id: str) -> None:
    found = session.scalar(
        select(AgentRunExecutionRow).where(
            AgentRunExecutionRow.agent_run_id == agent_run_id,
            AgentRunExecutionRow.execution_id == execution_id,
        )
    )
    if found is not None:
        return
    session.add(
        AgentRunExecutionRow(
            id=new_id(),
            agent_run_id=agent_run_id,
            execution_id=execution_id,
            created_at=utcnow(),
        )
    )


def list_executions_for_workflow(session: Session, workflow_id: str) -> list[Execution]:
    rows = session.scalars(select(ExecutionRow).where(ExecutionRow.workflow_id == workflow_id)).all()
    return [_execution(row) for row in rows]


def release_workflow_leases(session: Session, workflow_id: str) -> None:
    rows = session.scalars(select(RepositoryLeaseRow).where(RepositoryLeaseRow.holder_id == workflow_id)).all()
    for row in rows:
        session.delete(row)


def _execution_ids(session: Session, agent_run_id: str) -> list[str]:
    rows = session.scalars(select(AgentRunExecutionRow).where(AgentRunExecutionRow.agent_run_id == agent_run_id)).all()
    return [row.execution_id for row in rows]


def _agent_run(row: AgentRunRow, execution_ids: list[str]) -> AgentRun:
    ids = list(row.execution_ids or [])
    for execution_id in execution_ids:
        if execution_id not in ids:
            ids.append(execution_id)
    return AgentRun(
        id=row.id,
        workflow_id=row.workflow_id,
        project_id=row.project_id,
        task_id=row.task_id,
        agent_name=row.agent_name,
        agent_version=row.agent_version,
        status=AgentRunStatus(row.status),
        input_artifact_ids=list(row.input_artifact_ids or []),
        output_artifact_ids=list(row.output_artifact_ids or []),
        execution_ids=ids,
        input_snapshot=dict(row.input_snapshot or {}),
        output_snapshot=dict(row.output_snapshot or {}),
        error_code=row.error_code,
        error_message=row.error_message,
        idempotency_key=row.idempotency_key,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _execution(row: ExecutionRow) -> Execution:
    return Execution(
        id=row.id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        task_id=row.task_id,
        agent_id=row.agent_id,
        status=row.status,
        session_id=row.session_id,
        workspace_id=row.workspace_id,
        conversation_id=row.conversation_id,
        container_id=row.container_id,
        operation_id=row.operation_id,
        attempt=row.attempt,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _approval(row: ApprovalRow) -> Approval:
    return Approval(
        id=row.id,
        project_id=row.project_id,
        workflow_id=row.workflow_id,
        subject=row.subject,
        status=row.status,
        comment=row.comment,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
