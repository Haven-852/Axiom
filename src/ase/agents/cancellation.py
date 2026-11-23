from ase.db.session import session_scope
from ase.errors import AgentCancelledError
from ase.store import get_workflow


class CancellationContext:
    def __init__(self, sessions, workflow_id: str) -> None:
        self.sessions = sessions
        self.workflow_id = workflow_id
        self._requested = False

    def request(self) -> None:
        self._requested = True

    async def is_cancelled(self) -> bool:
        if self._requested:
            return True
        with session_scope(self.sessions) as session:
            row = get_workflow(session, self.workflow_id)
        return row is not None and row.status == "cancelled"

    async def raise_if_cancelled(self) -> None:
        if await self.is_cancelled():
            raise AgentCancelledError("workflow is cancelled")
