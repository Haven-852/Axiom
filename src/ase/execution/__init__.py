"""Execution state machine. OpenHands types do not belong here."""

_NEXT: dict[str, set[str]] = {
    "created": {"queued", "starting", "cancelled"},
    "queued": {"starting", "cancelled"},
    "starting": {"running", "failed", "cancelled"},
    "running": {"completed", "failed", "interrupted", "cancelled", "timeout"},
    "interrupted": {"running", "failed", "cancelled"},
    "timeout": {"running", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}

_LEGACY = {"succeeded": "completed", "timed_out": "timeout", "pending": "queued"}


def canonical_status(value: str) -> str:
    return _LEGACY.get(value, value)


def assert_transition(current: str, new: str) -> None:
    source = canonical_status(current)
    target = canonical_status(new)
    allowed = _NEXT.get(source)
    if allowed is None or target not in allowed:
        raise ValueError(f"illegal execution transition: {source} -> {target}")
