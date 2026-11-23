from collections.abc import Callable

from ase.contracts.domain import AgentSpec, RequirementAnalysis, DevelopmentPlan, EvaluationReport
from ase.errors import AgentAlreadyRegistered, AgentNotFoundError


def default_agents() -> list[AgentSpec]:
    schemas = {
        "research": (RequirementAnalysis.model_json_schema(), {"statement": {"type": "string"}}),
        "planning": (DevelopmentPlan.model_json_schema(), RequirementAnalysis.model_json_schema()),
        "coding": ({"type": "object"}, {"instruction": {"type": "string"}}),
        "testing": ({"passed": {"type": "boolean"}}, {"workspace": {"type": "string"}}),
        "diagnosis": ({"findings": {"type": "array"}}, {"output": {"type": "string"}}),
        "evaluation": (EvaluationReport.model_json_schema(), {"diff": {"type": "string"}}),
    }
    catalog = [
        ("research", "Research Agent", "research", "Turns a requirement into acceptance criteria.", "ase.lightweight", []),
        ("planning", "Planning Agent", "planning", "Turns an analysis into a task DAG.", "ase.lightweight", []),
        ("coding", "Coding Agent", "coding", "Edits the isolated workspace through the coding adapter.", "openhands", ["file_editor", "terminal"]),
        ("testing", "Testing Agent", "testing", "Runs pytest inside the sandbox.", "docker", ["pytest"]),
        ("diagnosis", "Diagnosis Agent", "diagnosis", "Reads a failed test log and returns a repair note.", "ase.lightweight", []),
        ("evaluation", "Evaluation Agent", "evaluation", "Scores the diff and the test result.", "ase.lightweight", []),
    ]
    specs: list[AgentSpec] = []
    for agent_id, name, agent_type, description, runtime, tools in catalog:
        output_schema, input_schema = schemas[agent_id]
        specs.append(
            AgentSpec(
                agent_id=agent_id,
                name=name,
                agent_type=agent_type,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
                runtime=runtime,
                model="none" if runtime != "openhands" else "configured",
                tool_permissions=tools,
                timeout_seconds=600 if agent_id == "coding" else 120,
                retry_limit=2 if agent_id == "coding" else 0,
                max_retries=2 if agent_id == "coding" else 0,
                capabilities=tools,
            )
        )
    return specs


class AgentRegistry:
    """In-process catalog. It does not start containers or conversations."""

    def __init__(self) -> None:
        self._definitions: dict[str, AgentSpec] = {}
        self._factories: dict[str, Callable[[], object]] = {}

    def register(self, definition: AgentSpec, factory: Callable[[], object]) -> None:
        if not definition.enabled or not definition.version:
            raise ValueError("agent definition is incomplete")
        if not definition.input_schema or not definition.output_schema:
            raise ValueError("agent definition is incomplete")
        if definition.agent_id in self._definitions or any(item.name == definition.name for item in self._definitions.values()):
            raise AgentAlreadyRegistered(definition.agent_id)
        self._definitions[definition.agent_id] = definition
        self._factories[definition.agent_id] = factory

    def get(self, name: str):
        factory = self._factories.get(name)
        if factory is None:
            raise AgentNotFoundError(name)
        return factory()

    def get_definition(self, name: str) -> AgentSpec:
        definition = self._definitions.get(name)
        if definition is None:
            raise AgentNotFoundError(name)
        return definition

    def list_agents(self) -> list[AgentSpec]:
        return list(self._definitions.values())

    def is_registered(self, name: str) -> bool:
        return name in self._definitions
