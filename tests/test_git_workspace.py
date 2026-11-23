import subprocess

from ase.infra.git_workspace import GitWorkspaceManager


def test_worktree_is_not_main(settings, repo) -> None:
    manager = GitWorkspaceManager(settings)
    info = manager.create_workspace("project", "task", str(repo), "HEAD")
    branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=info.path, text=True).strip()
    assert branch == "ase/task"
    (repo_file := __import__("pathlib").Path(info.path) / "calculator.py").write_text(
        repo_file.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    commit = manager.commit_changes(info, "task change")
    assert commit
    main = subprocess.check_output(["git", "rev-parse", "main"], cwd=repo, text=True).strip()
    assert commit != main
    diff = manager.get_diff(info)
    assert "calculator.py" in diff or diff == ""
    with __import__("pytest").raises(PermissionError):
        manager.merge_to_main(info)
    manager.cleanup_workspace(info, str(repo))
    assert not __import__("pathlib").Path(info.path).exists()


def test_commit_leaves_agent_runtime_files_untracked(settings, repo) -> None:
    manager = GitWorkspaceManager(settings)
    info = manager.create_workspace("project", "runtime", str(repo), "HEAD")
    root = __import__("pathlib").Path(info.path)
    source = root / "calculator.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    runtime = root / "workspace" / "conversations" / "abc" / "events"
    runtime.mkdir(parents=True)
    marker = runtime / (".eventlog-" + ("n" * 80))
    marker.write_text("runtime", encoding="utf-8")
    commit = manager.commit_changes(info, "task change without runtime files")
    listed = subprocess.check_output(["git", "-c", "core.longpaths=true", "ls-tree", "-r", "--name-only", commit], cwd=root, text=True)
    assert "calculator.py" in listed
    assert "workspace/conversations" not in listed
    assert marker.exists()
    manager.cleanup_workspace(info, str(repo))


def test_workspace_cannot_cover_ase_source(tmp_path, settings) -> None:
    settings.workspace_root = tmp_path / "workspaces"
    manager = GitWorkspaceManager(settings)
    blocked = settings.ase_source_root / "src"
    blocked.mkdir(parents=True, exist_ok=True)
    with __import__("pytest").raises(PermissionError):
        from ase.infra.policy import assert_workspace_allowed

        assert_workspace_allowed(blocked, settings)
    del manager
