from pathlib import Path

from ase.adapters.coding import CodingAgentAdapter
from ase.contracts.domain import (
    AgentRunResult,
    AgentSession,
    AgentTaskRequest,
    ExecutionStatus,
    new_id,
)


_IMPLEMENTATION = '''\
"""Small expression calculator. Scientific notation is accepted by the literal parser."""

import ast


_ALLOWED = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.UAdd,
    ast.USub,
)


def evaluate(expression: str) -> float:
    """Evaluate a numeric expression, including scientific notation such as 1.5e2."""
    rewritten = expression.replace("^", "**")
    tree = ast.parse(rewritten, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("only numbers are allowed")
    return float(eval(compile(tree, "<calculator>", "eval"), {"__builtins__": {}}, {}))
'''

_TEST = '''\
from calculator import evaluate


def test_scientific_notation() -> None:
    assert evaluate("1.5e2 + 2") == 152
    assert evaluate("2.5E-1 * 4") == 1
'''


class ScriptedCodingAdapter(CodingAgentAdapter):
    """Deterministic stand-in used when no OpenHands model is configured."""

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._events: dict[str, list[dict]] = {}
        self._status: dict[str, str] = {}

    async def create_session(self, workspace_path: str) -> AgentSession:
        session = AgentSession(session_id=new_id(), workspace_path=workspace_path, runtime="scripted")
        self._sessions[session.session_id] = session
        self._events[session.session_id] = []
        self._status[session.session_id] = "idle"
        return session

    async def execute_task(self, task: AgentTaskRequest, session: AgentSession) -> AgentRunResult:
        self._status[session.session_id] = "running"
        root = Path(session.workspace_path)
        (root / "calculator.py").write_text(_IMPLEMENTATION, encoding="utf-8")
        (root / "test_calculator.py").write_text(_TEST, encoding="utf-8")
        self._events[session.session_id].append(
            {"type": "code_changed", "path": "calculator.py", "task_id": task.task_id}
        )
        self._status[session.session_id] = "finished"
        return AgentRunResult(
            session_id=session.session_id,
            status=ExecutionStatus.SUCCEEDED,
            summary="scripted adapter wrote calculator.py and test_calculator.py",
            events=list(self._events[session.session_id]),
        )

    async def get_session_status(self, session_id: str) -> str:
        return self._status[session_id]

    async def get_execution_events(self, session_id: str) -> list[dict]:
        return list(self._events[session_id])

    async def interrupt(self, session_id: str) -> None:
        self._status[session_id] = "interrupted"

    async def close_session(self, session_id: str) -> None:
        self._status[session_id] = "closed"
