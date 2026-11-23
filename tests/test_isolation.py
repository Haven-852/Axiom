import threading

from ase.app import build_context
from ase.config import Settings
from ase.contracts.domain import Project, Requirement
from ase.db.session import session_scope
from ase.store import save_project, save_requirement
from ase.workflow.service import WorkflowService
from tests.doubles import GraphSandbox


def test_two_projects_do_not_share_workspaces(tmp_path, repo, settings) -> None:
    settings.workspace_root = tmp_path / "ws"
    build_context(settings, sandbox=GraphSandbox())
    second = tmp_path / "calculator-b"
    __import__("shutil").copytree(repo, second)
    errors: list[BaseException] = []
    results: list[dict] = []

    def run(name: str, repo_path) -> None:
        try:
            ctx = build_context(settings, sandbox=GraphSandbox())
            project = Project(name=name, repo_path=str(repo_path), base_ref="HEAD")
            requirement = Requirement(project_id=project.id, title=name, statement="增加科学计数法")
            with session_scope(ctx.sessions) as session:
                save_project(session, project)
                save_requirement(session, requirement)
            service = WorkflowService(ctx)
            paused = service.start(project.id, requirement.id, requirement.statement)
            finished = service.resume(paused["workflow_id"], "approved", "parallel")
            results.append(finished)
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("iso-1", repo)),
        threading.Thread(target=run, args=("iso-2", second)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(results) == 2
    paths = {item["values"]["workspace"]["path"] for item in results}
    branches = {item["values"]["workspace"]["branch"] for item in results}
    assert len(paths) == 2
    assert len(branches) == 2
    assert {item["status"] for item in results} == {"delivered"}
