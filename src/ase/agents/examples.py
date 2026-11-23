from ase.agents.base import BaseAgent
from ase.contracts.domain import AgentContext, AgentResult, AgentRunStatus, AgentSpec


def echo_definition() -> AgentSpec:
    body = {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}
    return AgentSpec(
        agent_id="echo",
        name="Echo Agent",
        agent_type="echo",
        description="Returns its structured input. It is an architecture fixture.",
        input_schema=body,
        output_schema=body,
        runtime="ase.fixture",
        model="none",
        tool_permissions=[],
        timeout_seconds=30,
        retry_limit=0,
        version="1",
        capabilities=["echo"],
        max_retries=0,
    )


def research_stub_definition() -> AgentSpec:
    return AgentSpec(
        agent_id="research_stub",
        name="Research Stub Agent",
        agent_type="research_stub",
        description="Returns a fixed structured report. It is not a Research Agent.",
        input_schema={"type": "object", "required": ["requirement"], "properties": {"requirement": {"type": "string"}}},
        output_schema={
            "type": "object",
            "required": ["summary", "findings"],
            "properties": {"summary": {"type": "string"}, "findings": {"type": "array"}},
        },
        runtime="ase.fixture",
        model="none",
        tool_permissions=[],
        timeout_seconds=30,
        retry_limit=0,
        version="1",
        capabilities=["structured-report"],
        max_retries=0,
    )


class EchoAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def version(self) -> str:
        return "1"

    async def run(self, context: AgentContext, input_data: dict) -> AgentResult:
        await context.raise_if_cancelled()
        return AgentResult(
            agent_name=self.name,
            agent_version=self.version,
            workflow_id=context.workflow_id,
            agent_run_id=context.agent_run_id,
            status=AgentRunStatus.COMPLETED,
            output={"text": input_data["text"]},
        )


class ResearchStubAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "research_stub"

    @property
    def version(self) -> str:
        return "1"

    async def run(self, context: AgentContext, input_data: dict) -> AgentResult:
        await context.raise_if_cancelled()
        requirement = input_data["requirement"]
        return AgentResult(
            agent_name=self.name,
            agent_version=self.version,
            workflow_id=context.workflow_id,
            agent_run_id=context.agent_run_id,
            status=AgentRunStatus.COMPLETED,
            output={"summary": f"stub report: {requirement}", "findings": ["architecture-only"]},
        )
