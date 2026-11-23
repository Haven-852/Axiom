from pathlib import Path

from ase.contracts.domain import TestResult


class GraphSandbox:
    """Workflow double. It does not start Docker and is not a container test."""

    def run_pytest(self, workspace: Path, execution_id: str, container_id: str | None = None) -> TestResult:
        text = (Path(workspace) / "calculator.py").read_text(encoding="utf-8")
        passed = "raise RuntimeError('broken')" not in text and "def evaluate" in text
        return TestResult(
            execution_id=execution_id,
            passed=passed,
            command=["graph-double", "pytest"],
            exit_code=0 if passed else 1,
            output="graph double: passed" if passed else "graph double: broken implementation",
            duration_seconds=0.01,
        )
