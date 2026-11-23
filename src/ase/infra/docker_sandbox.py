import re
import time
from pathlib import Path

from ase.config import Settings
from ase.contracts.domain import TestResult
from ase.infra.agent_container import AgentContainerManager
from ase.infra.policy import assert_workspace_allowed, review_command


class DockerSandbox:
    def __init__(self, settings: Settings, containers: AgentContainerManager) -> None:
        self.settings = settings
        self.containers = containers

    def build_pytest_args(self, workspace: Path) -> list[str]:
        resolved = assert_workspace_allowed(workspace, self.settings)
        args = [
            "docker",
            "exec",
            "agent-container",
            "python",
            "-m",
            "pytest",
            "-q",
        ]
        joined = " ".join(args)
        if "--privileged" in args or "docker.sock" in joined or str(resolved) == "":
            raise PermissionError("sandbox command violates the isolation policy")
        decision = review_command(joined)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return args

    def run_pytest(self, workspace: Path, execution_id: str, container_id: str | None = None) -> TestResult:
        assert_workspace_allowed(workspace, self.settings)
        if not container_id:
            return TestResult(
                execution_id=execution_id,
                passed=False,
                command=["docker", "exec", "missing", "python", "-m", "pytest", "-q"],
                exit_code=127,
                output="pytest requires the agent container. Host execution is disabled.",
                duration_seconds=0,
            )
        started = time.perf_counter()
        code, output, elapsed = self.containers.execute(
            container_id,
            ["python", "-m", "pytest", "-q"],
            timeout=self.settings.sandbox_timeout_seconds,
        )
        if "No such container" in output or "is not running" in output:
            handle = self.containers.start(workspace)
            container_id = handle.container_id
            code, output, elapsed = self.containers.execute(
                container_id,
                ["python", "-m", "pytest", "-q"],
                timeout=self.settings.sandbox_timeout_seconds,
            )
        passed_count, failed_count = _counts(output)
        return TestResult(
            execution_id=execution_id,
            passed=code == 0,
            command=["docker", "exec", container_id, "python", "-m", "pytest", "-q"],
            exit_code=code,
            output=output,
            duration_seconds=elapsed or round(time.perf_counter() - started, 3),
            passed_count=passed_count,
            failed_count=failed_count,
        )


def _counts(output: str) -> tuple[int | None, int | None]:
    passed = re.search(r"(\d+) passed", output)
    failed = re.search(r"(\d+) failed", output)
    return (int(passed.group(1)) if passed else None, int(failed.group(1)) if failed else None)
