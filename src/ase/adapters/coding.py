from abc import ABC, abstractmethod

from ase.contracts.domain import AgentRunResult, AgentSession, AgentTaskRequest


class CodingAgentAdapter(ABC):
    @abstractmethod
    async def create_session(self, workspace_path: str) -> AgentSession:
        raise NotImplementedError

    @abstractmethod
    async def execute_task(self, task: AgentTaskRequest, session: AgentSession) -> AgentRunResult:
        raise NotImplementedError

    @abstractmethod
    async def get_session_status(self, session_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def get_execution_events(self, session_id: str) -> list[dict]:
        raise NotImplementedError

    @abstractmethod
    async def interrupt(self, session_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close_session(self, session_id: str) -> None:
        raise NotImplementedError
