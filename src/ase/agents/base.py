from abc import ABC, abstractmethod

from ase.contracts.domain import AgentContext, AgentResult


class BaseAgent(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def run(self, context: AgentContext, input_data: dict) -> AgentResult:
        raise NotImplementedError
