from pathlib import Path

from ase.config import Settings
from ase.contracts.domain import CommandPolicy


_L3_FRAGMENTS = (
    "docker.sock",
    "--privileged",
    "rm -rf",
    "mkfs",
    "shutdown",
    "reboot",
    ":(){",
    "sudo",
    "chmod",
    "chown",
    "git merge",
    "git push",
)
_L2_MARKERS = ("pytest", "ruff", "mypy")


def assert_workspace_allowed(path: Path, settings: Settings) -> Path:
    resolved = path.resolve()
    root = settings.workspace_root.resolve()
    if root not in resolved.parents and resolved != root:
        raise PermissionError(f"workspace {resolved} is outside {root}")
    source = settings.ase_source_root.resolve()
    protected = [source / "src", source / "docs", source / "analysis", source / "tests"]
    for item in protected:
        if resolved == item or item in resolved.parents:
            raise PermissionError(f"workspace {resolved} overlaps ASE source at {item}")
    return resolved


def classify_command(command: str) -> str:
    lowered = command.lower()
    for fragment in _L3_FRAGMENTS:
        if fragment in lowered:
            return "L3"
    for marker in _L2_MARKERS:
        if marker in lowered:
            return "L2"
    return "L1"


def review_command(command: str) -> CommandPolicy:
    level = classify_command(command)
    if level == "L3":
        return CommandPolicy(allowed=False, reason=f"L3 command requires human approval: {command}")
    return CommandPolicy(allowed=True, reason=level)
