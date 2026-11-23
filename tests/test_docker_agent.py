import uuid
from pathlib import Path

import pytest

from ase.config import Settings
from ase.infra.agent_container import AgentContainerManager
from ase.infra.docker_sandbox import DockerSandbox


def test_agent_container_limits_files_and_pytest(tmp_path: Path) -> None:
    probe = __import__("subprocess").run(["docker", "image", "inspect", "ghcr.io/openhands/agent-server:1.63.1-python"], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("agent-server image is not present")
    workspace = tmp_path / "workspaces" / "proj" / "task"
    workspace.mkdir(parents=True)
    (workspace / "test_calc.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    settings = Settings(workspace_root=tmp_path / "workspaces", ase_source_root=tmp_path / "ase-source")
    (tmp_path / "ase-source").mkdir()
    manager = AgentContainerManager(settings)
    handle = manager.start(workspace)
    try:
        assert handle.limits["privileged"] is False
        assert handle.limits["memory"] > 0
        assert handle.limits["nano_cpus"] > 0
        assert "ALL" in handle.limits["cap_drop"]
        assert not any("docker.sock" in bind for bind in handle.limits["binds"])
        marker = str(uuid.uuid4())
        code, output, _elapsed = manager.execute(
            handle.container_id,
            ["python", "-c", f"open('/workspace/marker.txt','w').write('{marker}')"],
            timeout=30,
        )
        assert code == 0, output
        assert (workspace / "marker.txt").read_text(encoding="utf-8") == marker
        result = DockerSandbox(settings, manager).run_pytest(workspace, "exec", handle.container_id)
        assert result.command[0:2] == ["docker", "exec"]
        assert result.exit_code == 0, result.output
        assert result.passed
        assert result.duration_seconds >= 0
        from openhands.sdk.workspace import RemoteWorkspace

        remote = RemoteWorkspace(host=handle.host, working_dir="/workspace", api_key=handle.session_api_key)
        command = remote.execute_command("python -c \"print('remote-ok')\"", cwd="/workspace", timeout=30)
        assert command.exit_code == 0
        assert "remote-ok" in (command.stdout or "")
    finally:
        manager.stop(handle.container_id)
