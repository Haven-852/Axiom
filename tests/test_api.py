from fastapi.testclient import TestClient

from ase.app import build_context, create_app
from ase.contracts.domain import Project, Requirement
from ase.db.session import session_scope
from ase.store import save_project, save_requirement


def test_api_approval_and_queries(settings) -> None:
    from tests.doubles import GraphSandbox

    ctx = build_context(settings, sandbox=GraphSandbox())
    project = Project(name="calculator", repo_path=str(settings.ase_source_root), base_ref="HEAD")
    requirement = Requirement(project_id=project.id, title="科学计数法", statement="为计算器增加科学计数法表达式计算，并补充单元测试。")
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
    client = TestClient(create_app(settings, ctx))
    denied = client.get("/api/v1/approvals")
    assert denied.status_code == 401
    created = client.post("/api/v1/workflows", json={"project_id": project.id, "requirement_id": requirement.id})
    assert created.status_code == 200
    body = created.json()["data"]
    assert body["status"] == "waiting_approval"
    approved = client.post(
        f"/api/v1/approvals/{body['approval_id']}/approve",
        headers={"X-ASE-API-KEY": "test-key"},
        json={"comment": "ok"},
    )
    assert approved.status_code == 200
    delivered = approved.json()["data"]
    assert delivered["status"] == "delivered"
    events = client.get(f"/api/v1/workflows/{body['workflow_id']}/events")
    assert events.status_code == 200
    assert any(item["event_type"] == "Delivery Completed" for item in events.json()["data"])
    execution_id = delivered["values"]["execution_id"]
    execution = client.get(f"/api/v1/executions/{execution_id}")
    assert execution.status_code == 200
    task_id = delivered["values"]["plan"]["tasks"][0]["id"]
    assert client.get(f"/api/v1/tasks/{task_id}").status_code == 200
