from ase.adapters.coding import CodingAgentAdapter
from ase.agents.base import BaseAgent
from ase.contracts.domain import AgentContext, AgentResult, AgentRunStatus, AgentSpec, ExecutionStatus
from ase.db.session import session_scope
from ase.execution.eligibility import evaluate
from ase.execution.service import ExecutionService
from ase.schemas.coding import CodingInput


def coding_definition() -> AgentSpec:
    return AgentSpec(
        agent_id="coding",
        name="Coding Agent",
        agent_type="coding",
        description="Delegates code changes to the existing execution port.",
        input_schema={
            "type": "object",
            "required": ["instruction", "workspace_path", "task_id"],
            "properties": {
                "instruction": {"type": "string"},
                "workspace_path": {"type": "string"},
                "task_id": {"type": "string"},
            },
        },
        output_schema={
            "type": "object",
            "required": ["summary", "execution_status"],
            "properties": {"summary": {"type": "string"}, "execution_status": {"type": "string"}},
        },
        runtime="openhands",
        model="configured",
        tool_permissions=["file_editor", "terminal"],
        timeout_seconds=600,
        retry_limit=0,
        version="1",
        capabilities=["workspace-edit"],
        max_retries=0,
    )


class CodingAgent(BaseAgent):
    """Records a coding run and delegates effects to ExecutionService."""

    def __init__(self, adapter: CodingAgentAdapter, sessions) -> None:
        self.adapter = adapter
        self.sessions = sessions

    @property
    def name(self) -> str:
        return "coding"

    @property
    def version(self) -> str:
        return "1"

    async def run(self, context: AgentContext, input_data: dict) -> AgentResult:
        await context.raise_if_cancelled()
        if input_data.get("plan_id"):
            parsed = CodingInput.model_validate(input_data)
            with session_scope(self.sessions) as session:
                evaluate(
                    session,
                    workflow_id=parsed.workflow_id,
                    task_id=parsed.task_id,
                    allow_claimed=True,
                )
            input_data = {**input_data, "instruction": parsed.instruction, "workspace_path": parsed.workspace_path, "task_id": parsed.task_id}
        service = ExecutionService(self.adapter, self.sessions)
        execution, outcome = await service.run_claimed(
            project_id=context.project_id,
            workflow_id=context.workflow_id,
            task_id=context.task_id or input_data["task_id"],
            instruction=input_data["instruction"],
            workspace_path=input_data["workspace_path"],
            agent_run_id=context.agent_run_id,
            conversation_id=input_data.get("conversation_id") or "",
        )
        await context.raise_if_cancelled()
        status = AgentRunStatus.COMPLETED if outcome.status == ExecutionStatus.COMPLETED else AgentRunStatus.FAILED
        return AgentResult(
            agent_name=self.name,
            agent_version=self.version,
            workflow_id=context.workflow_id,
            agent_run_id=context.agent_run_id,
            status=status,
            output={
                "summary": outcome.summary,
                "execution_status": outcome.status.value,
            },
            execution_ids=[execution.id],
            error_message=outcome.error,
        )
