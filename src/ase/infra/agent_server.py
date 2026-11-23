from pathlib import Path

from ase.config import Settings
from ase.infra.policy import assert_workspace_allowed


def agent_server_run_args(settings: Settings, workspace: Path, host_port: int) -> list[str]:
    """Hardened container command for a pinned agent-server image.

    OpenHands DockerWorkspace does not set memory, cpu, or capability limits.
    ASE owns this command and then attaches with RemoteWorkspace.
    """
    resolved = assert_workspace_allowed(workspace, settings)
    args = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        f"ase-agent-server-{host_port}",
        f"--memory={settings.sandbox_memory}",
        f"--cpus={settings.sandbox_cpus}",
        f"--pids-limit={settings.sandbox_pids_limit}",
        "--cap-drop=ALL",
        "--security-opt",
        "no-new-privileges",
        "--network",
        settings.agent_server_network,
        "-p",
        f"127.0.0.1:{host_port}:8000",
        "-v",
        f"{resolved}:/workspace:rw",
        "-w",
        "/workspace",
        settings.agent_server_image,
    ]
    rendered = " ".join(args)
    if "--privileged" in args or "docker.sock" in rendered:
        raise PermissionError("agent server command violates the isolation policy")
    return args
