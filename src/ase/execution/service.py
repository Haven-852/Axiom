"""Facade over the existing coding port and operation ledger.

The production LangGraph node keeps its crash points and conversation resume.
This service is the agent-layer entry to the same port.
"""

from ase.adapters.coding import CodingAgentAdapter
from ase.contracts.domain import AgentRunResult, AgentSession, AgentTaskRequest, Execution, ExecutionStatus, OperationRecord, new_id
from ase.db.session import session_scope
from ase.store import claim_operation, link_agent_execution, save_execution, save_operation, update_execution


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
            conversation_id=operation.conversation_id or conversation_id or None,
        )
        with session_scope(self.sessions) as session:
            if operation.execution_id is None:
                save_execution(session, execution)
            operation.execution_id = execution.id
            operation.conversation_id = execution.conversation_id
            operation.status = "running"
            save_operation(session, operation)
            link_agent_execution(session, agent_run_id, execution.id)
        task = AgentTaskRequest(
            task_id=task_id,
            instruction=instruction,
            workspace_path=workspace_path,
            conversation_id=execution.conversation_id or "",
        )
        agent_session, outcome = await self.execute(task, workspace_path)
        execution.status = outcome.status
        execution.session_id = outcome.session_id or agent_session.session_id
        execution.conversation_id = outcome.conversation_id or execution.conversation_id
        execution.error = outcome.error
        operation.status = "completed" if outcome.status == ExecutionStatus.SUCCEEDED else "failed"
        operation.execution_id = execution.id
        operation.conversation_id = execution.conversation_id
        with session_scope(self.sessions) as session:
            update_execution(session, execution)
            save_operation(session, operation)
        await self.coding.close_session(agent_session.session_id)
        return execution, outcome

    async def propagate_cancel(self, session_id: str | None, container_id: str | None, containers) -> None:
        if session_id:
            try:
                await self.coding.interrupt(session_id)
            except KeyError:
                return
        if container_id and containers is not None:
            containers.stop(container_id)
