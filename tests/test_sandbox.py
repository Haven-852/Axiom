from pathlib import Path

from ase.infra.agent_container import AgentContainerManager, assert_container_limits
from ase.infra.agent_server import agent_server_run_args
from ase.infra.docker_sandbox import DockerSandbox


def test_pytest_command_is_constrained(settings, tmp_path: Path) -> None:
    workspace = settings.workspace_root / "demo"
    workspace.mkdir(parents=True)
    args = DockerSandbox(settings, AgentContainerManager(settings)).build_pytest_args(workspace)
    rendered = " ".join(args)
    assert args[:2] == ["docker", "exec"]
    assert "--privileged" not in args
    assert "docker.sock" not in rendered
    assert "python" in args and "pytest" in args


def test_container_limits_reject_a_privileged_inspect(settings) -> None:
    workspace = settings.workspace_root / "limits"
    workspace.mkdir(parents=True)
    payload = {
        "HostConfig": {
            "Privileged": True,
            "Memory": 1,
            "NanoCpus": 1,
            "PidsLimit": 1,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Binds": [f"{workspace.resolve()}:/workspace:rw"],
        },
        "Config": {"User": ""},
    }
    with __import__("pytest").raises(PermissionError):
        assert_container_limits(payload, workspace)


def test_agent_run_args_are_constrained(settings) -> None:
    workspace = settings.workspace_root / "run"
    workspace.mkdir(parents=True)
    args = AgentContainerManager(settings).build_run_args(workspace, 18011, "ase-test", "session-key")
    rendered = " ".join(args)
    assert "--privileged" not in args
    assert "docker.sock" not in rendered
    assert "--cap-drop=ALL" in args
    assert "no-new-privileges" in args
    assert f"--memory={settings.agent_server_memory}" in args
    assert settings.agent_server_image in args


def test_agent_server_command_is_constrained(settings, tmp_path: Path) -> None:
    workspace = settings.workspace_root / "agent"
    workspace.mkdir(parents=True)
    args = agent_server_run_args(settings, workspace, 18000)
    rendered = " ".join(args)
    assert "--privileged" not in args
    assert "docker.sock" not in rendered
    assert "--cap-drop=ALL" in args
    assert settings.agent_server_image in args
    assert str(workspace) in rendered
