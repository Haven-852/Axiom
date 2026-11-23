import subprocess
from pathlib import Path

import pytest

from ase.config import Settings
from ase.infra.agent_container import AgentContainerManager


def test_readonly_root_keeps_limits_and_workspace_write(tmp_path: Path) -> None:
    image = "ase-agent-server:1.63.1"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        pytest.skip("local agent image is not built")
    settings = Settings(
        workspace_root=tmp_path / "ws",
        ase_source_root=tmp_path / "source",
        agent_server_image=image,
        agent_readonly_root=True,
        coding_runtime="scripted",
        llm_api_key="",
    )
    (tmp_path / "source").mkdir()
    workspace = settings.workspace_root / "task"
    workspace.mkdir(parents=True)
    manager = AgentContainerManager(settings)
    handle = manager.start(workspace)
    try:
        assert handle.limits["readonly_rootfs"] is True
        assert handle.limits["privileged"] is False
        assert "ALL" in handle.limits["cap_drop"]
        code, output, _elapsed = manager.execute(handle.container_id, ["python", "-c", "open('/workspace/marker.txt','w').write('ok')"], timeout=30)
        assert code == 0, output
        assert (workspace / "marker.txt").read_text(encoding="utf-8") == "ok"
        version, version_output, _elapsed = manager.execute(handle.container_id, ["python", "-m", "pytest", "--version"], timeout=30)
        assert version == 0, version_output
    finally:
        manager.stop_all()


def test_two_agent_containers_stay_isolated(tmp_path: Path) -> None:
    image = "ase-agent-server:1.63.1"
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0:
        pytest.skip("local agent image is not built")
    settings = Settings(
        workspace_root=tmp_path / "ws",
        ase_source_root=tmp_path / "source",
        agent_server_image=image,
        agent_readonly_root=True,
        coding_runtime="scripted",
        llm_api_key="",
    )
    (tmp_path / "source").mkdir()
    first_dir = settings.workspace_root / "one"
    second_dir = settings.workspace_root / "two"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    manager = AgentContainerManager(settings)
    first = manager.start(first_dir)
    second = manager.start(second_dir)
    try:
        assert first.container_id != second.container_id
        assert first.limits["memory"] == 2147483648
        assert second.limits["memory"] == 2147483648
        assert first.limits["readonly_rootfs"] is True
        assert second.limits["readonly_rootfs"] is True
        manager.execute(first.container_id, ["python", "-c", "open('/workspace/a.txt','w').write('one')"], timeout=30)
        manager.execute(second.container_id, ["python", "-c", "open('/workspace/b.txt','w').write('two')"], timeout=30)
        assert (first_dir / "a.txt").read_text(encoding="utf-8") == "one"
        assert not (first_dir / "b.txt").exists()
        assert (second_dir / "b.txt").read_text(encoding="utf-8") == "two"
        assert not (second_dir / "a.txt").exists()
    finally:
        manager.stop_all()
