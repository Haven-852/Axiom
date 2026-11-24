"""Facade over the existing coding port and operation ledger.

The production LangGraph node keeps its crash points and conversation resume.
This service is the agent-layer entry to the same port.
"""

import uuid

from ase.adapters.coding import CodingAgentAdapter
from ase.contracts.domain import (
    AgentRunResult,
    AgentSession,
    AgentTaskRequest,
    EventSource,
    Execution,
    ExecutionEvent,
    ExecutionStatus,
    ModelUsage,
    OperationRecord,
    new_id,
)
from ase.db.session import session_scope
from ase.store import (
    claim_operation,
    link_agent_execution,
    save_event,
    save_execution,
    save_model_usage,
    save_operation,
    update_execution,
)


class ExecutionService:
    def __init__(self, coding: CodingAgentAdapter, sessions) -> None:
        self.coding = coding
        self.sessions = sessions

    async def execute(self, task: AgentTaskRequest, workspace_path: str) -> tuple[AgentSession, AgentRunResult]:
        session = await self.coding.create_session(workspace_path)
        outcome = await self.coding.execute_task(task, session)
        return session, outcome

    async def run_claimed(
        self,
        *,
        project_id: str,
        workflow_id: str,
        task_id: str,
        instruction: str,
        workspace_path: str,
        agent_run_id: str,
        conversation_id: str = "",
        attempt: int = 1,
        kind: str = "develop",
    ) -> tuple[Execution, AgentRunResult]:
        operation = OperationRecord(
            id=f"{workflow_id}:{task_id}:{kind}:{attempt}",
            project_id=project_id,
            workflow_id=workflow_id,
            task_id=task_id,
            kind=kind,
            attempt=attempt,
            status="running",
        )
        with session_scope(self.sessions) as session:
            operation = claim_operation(session, operation)
        if operation.status == "completed" and operation.execution_id:
            with session_scope(self.sessions) as session:
                link_agent_execution(session, agent_run_id, operation.execution_id)
            return (
                Execution(
                    id=operation.execution_id,
                    project_id=project_id,
                    workflow_id=workflow_id,
                    task_id=task_id,
                    agent_id="coding",
                    status=ExecutionStatus.COMPLETED,
                    operation_id=operation.id,
                    attempt=attempt,
                    conversation_id=operation.conversation_id,
                ),
                AgentRunResult(
                    session_id=operation.execution_id,
                    status=ExecutionStatus.COMPLETED,
                    summary="completed operation skipped",
                    conversation_id=operation.conversation_id,
                ),
            )
        execution = Execution(
            id=operation.execution_id or new_id(),
            project_id=project_id,
            workflow_id=workflow_id,
            task_id=task_id,
            agent_id="coding",
            status=ExecutionStatus.RUNNING,
            operation_id=operation.id,
            attempt=attempt,
            conversation_id=operation.conversation_id or conversation_id or str(uuid.uuid4()),
        )
        with session_scope(self.sessions) as session:
            if operation.execution_id is None:
                save_execution(session, execution)
                operation.execution_id = execution.id
                operation.conversation_id = execution.conversation_id
                operation.status = "running"
                save_operation(session, operation)
            link_agent_execution(session, agent_run_id, execution.id)
        self._watch(execution)
        task = AgentTaskRequest(
            task_id=task_id,
            instruction=instruction,
            workspace_path=workspace_path,
            conversation_id=execution.conversation_id or "",
        )
        try:
            agent_session, outcome = await self.execute(task, workspace_path)
        finally:
            self._clear_watch()
        execution.status = outcome.status
        execution.session_id = outcome.session_id or agent_session.session_id
        execution.conversation_id = outcome.conversation_id or execution.conversation_id
        execution.error = outcome.error or (None if outcome.status == ExecutionStatus.SUCCEEDED else outcome.summary)
        container_id = getattr(self.coding, "last_container_id", None)
        if container_id:
            execution.container_id = container_id
        operation.status = "completed" if outcome.status == ExecutionStatus.SUCCEEDED else "failed"
        operation.execution_id = execution.id
        operation.conversation_id = execution.conversation_id
        with session_scope(self.sessions) as session:
            update_execution(session, execution)
            save_operation(session, operation)
            self._store_events(session, execution, outcome.events)
            self._store_usage(session, execution, agent_run_id, outcome.usage)
        await self.coding.close_session(agent_session.session_id)
        return execution, outcome

    def _watch(self, execution: Execution) -> None:
        coding = self.coding
        container_id = getattr(coding, "last_container_id", None)
        if container_id:
            execution.container_id = container_id
        if not hasattr(coding, "on_step"):
            return

        def _on_step(conversation_id: str, events: list[dict]) -> None:
            execution.conversation_id = conversation_id or execution.conversation_id
            with session_scope(self.sessions) as session:
                update_execution(session, execution)
                self._store_events(session, execution, events)

        coding.on_step = _on_step

    def _clear_watch(self) -> None:
        if hasattr(self.coding, "on_step"):
            self.coding.on_step = None

    def _store_events(self, session, execution: Execution, events: list[dict]) -> None:
        from sqlalchemy import func, select

        from ase.db.orm import ExecutionEventRow

        stored = session.scalar(
            select(func.count()).select_from(ExecutionEventRow).where(ExecutionEventRow.execution_id == execution.id)
        ) or 0
        for event in events[stored:]:
            save_event(
                session,
                ExecutionEvent(
                    project_id=execution.project_id,
                    workflow_id=execution.workflow_id,
                    task_id=execution.task_id,
                    execution_id=execution.id,
                    event_type=str(event.get("type") or "Agent Event"),
                    source=EventSource.OPENHANDS,
                    payload=event,
                ),
            )

    def _store_usage(self, session, execution: Execution, agent_run_id: str, usage: dict | None) -> None:
        counts = _token_counts(usage)
        if counts is None:
            return
        prompt, completion, total = counts
        save_model_usage(
            session,
            ModelUsage(
                project_id=execution.project_id,
                workflow_id=execution.workflow_id,
                task_id=execution.task_id,
                execution_id=execution.id,
                provider="openhands",
                model=_usage_model(usage, self.coding),
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
                agent_name="coding",
                agent_run_id=agent_run_id,
            ),
        )

    async def propagate_cancel(self, session_id: str | None, container_id: str | None, containers) -> None:
        if session_id:
            try:
                await self.coding.interrupt(session_id)
            except KeyError:
                return
        if container_id and containers is not None:
            containers.stop(container_id)


def _usage_model(usage: dict | None, coding) -> str:
    if isinstance(usage, dict) and usage.get("model"):
        return str(usage["model"])
    configured = getattr(getattr(coding, "settings", None), "llm_model", None)
    return str(configured or "unknown")


def _token_counts(usage: dict | None) -> tuple[int, int, int] | None:
    if not isinstance(usage, dict):
        return None
    found = _find_tokens(usage)
    if found is None:
        return None
    prompt, completion, total = found
    if prompt is None and completion is None and total is None:
        return None
    prompt = prompt or 0
    completion = completion or 0
    return prompt, completion, total if total is not None else prompt + completion


def _find_tokens(value: dict) -> tuple[int | None, int | None, int | None] | None:
    prompt = _as_int(value.get("prompt_tokens"))
    completion = _as_int(value.get("completion_tokens"))
    total = _as_int(value.get("total_tokens"))
    if prompt is not None or completion is not None or total is not None:
        return prompt, completion, total
    for nested in value.values():
        if isinstance(nested, dict):
            found = _find_tokens(nested)
            if found is not None:
                return found
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
