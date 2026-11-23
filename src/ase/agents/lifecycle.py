"""AgentRun state machine. It does not replace the Execution state machine."""

_NEXT: dict[str, set[str]] = {
    "pending": {"running", "cancelled"},
    "running": {"completed", "failed", "interrupted", "cancelled", "timeout"},
    "interrupted": {"running", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
    "timeout": set(),
}


def assert_agent_transition(current: str, new: str) -> None:
    allowed = _NEXT.get(current)
    if allowed is None or new not in allowed:
        raise ValueError(f"illegal agent run transition: {current} -> {new}")
