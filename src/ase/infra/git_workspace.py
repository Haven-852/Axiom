import subprocess
from pathlib import Path

from ase.config import Settings
from ase.contracts.domain import WorkspaceInfo, new_id
from ase.infra.policy import assert_workspace_allowed


class GitWorkspaceManager:
    """Creates a task branch in a git worktree. It never merges that branch."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create_workspace(self, project_id: str, task_id: str, repo_path: str, base_ref: str) -> WorkspaceInfo:
        repo = Path(repo_path).resolve()
        if not (repo / ".git").exists() and not _is_git(repo):
            raise RuntimeError(f"{repo} is not a git repository")
        workspace_id = new_id()
        branch = f"ase/{task_id}"
        path = self.settings.workspace_root.resolve() / project_id / task_id
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            current = _git(path, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
            if current != branch:
                raise RuntimeError(f"workspace path already exists: {path}")
        else:
            created = subprocess.run(
                ["git", "-c", "core.autocrlf=false", "worktree", "add", "-b", branch, str(path), base_ref],
                cwd=repo,
                check=False,
                capture_output=True,
                text=True,
            )
            if created.returncode != 0:
                reused = subprocess.run(
                    ["git", "-c", "core.autocrlf=false", "worktree", "add", str(path), branch],
                    cwd=repo,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if reused.returncode != 0:
                    detail = (created.stderr or reused.stderr or "worktree add failed").strip()
                    raise RuntimeError(detail)
        assert_workspace_allowed(path, self.settings)
        current = _git(path, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        if current in {"main", "master"}:
            raise RuntimeError("worktree checked out the main branch")
        return WorkspaceInfo(
            workspace_id=workspace_id,
            project_id=project_id,
            task_id=task_id,
            path=str(path),
            branch=branch,
            base_ref=base_ref,
        )

    def get_workspace(self, info: WorkspaceInfo) -> WorkspaceInfo:
        path = assert_workspace_allowed(Path(info.path), self.settings)
        if not path.exists():
            raise FileNotFoundError(path)
        return info

    def get_diff(self, info: WorkspaceInfo) -> str:
        path = assert_workspace_allowed(Path(info.path), self.settings)
        unstaged = _git(path, ["diff", "--"])
        staged = _git(path, ["diff", "--cached", "--"])
        base = _git(path, ["diff", f"{info.base_ref}...HEAD"])
        return "\n".join(part for part in (base, staged, unstaged) if part.strip())

    def commit_changes(self, info: WorkspaceInfo, message: str) -> str:
        path = assert_workspace_allowed(Path(info.path), self.settings)
        branch = _git(path, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        if branch in {"main", "master"}:
            raise PermissionError("refusing to commit on the main branch")
        _git(
            path,
            [
                "add",
                "-A",
                "--",
                ".",
                ":(exclude)workspace/conversations",
                ":(exclude)workspace/bash_events",
            ],
        )
        status = _git(path, ["status", "--porcelain"])
        if not status.strip():
            return _git(path, ["rev-parse", "HEAD"]).strip()
        _git(path, ["commit", "-m", message])
        return _git(path, ["rev-parse", "HEAD"]).strip()

    def cleanup_workspace(self, info: WorkspaceInfo, repo_path: str) -> None:
        path = Path(info.path)
        repo = Path(repo_path).resolve()
        if path.exists():
            _git(repo, ["worktree", "remove", "--force", str(path)])
        _git(repo, ["worktree", "prune"])

    def merge_to_main(self, info: WorkspaceInfo) -> None:
        del info
        raise PermissionError("merging to the main branch requires a separate human approval")


def _is_git(repo: Path) -> bool:
    try:
        _git(repo, ["rev-parse", "--is-inside-work-tree"])
        return True
    except RuntimeError:
        return False


def _git(cwd: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "core.longpaths=true", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout
