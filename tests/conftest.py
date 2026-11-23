import shutil
import subprocess
from pathlib import Path

import pytest

from ase.config import Settings


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[1] / "ase-poc-demo" / "calculator"
    project = tmp_path / "calculator"
    shutil.copytree(source, project)
    subprocess.run(["git", "init", "-b", "main"], cwd=project, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=ase@example.com", "-c", "user.name=ASE", "commit", "-m", "init"],
        cwd=project,
        check=True,
        capture_output=True,
    )
    return project


@pytest.fixture()
def settings(tmp_path: Path, repo: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'ase.db'}",
        workspace_root=tmp_path / "workspaces",
        api_key="test-key",
        allow_host_tests=True,
        coding_runtime="scripted",
        ase_source_root=repo,
    )
