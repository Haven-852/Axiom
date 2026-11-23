from pathlib import Path

from ase.adapters.scripted import ScriptedCodingAdapter, _TEST
from ase.app import build_context
from ase.contracts.domain import Project, Requirement
from ase.db.session import session_scope
from ase.store import save_project, save_requirement
from ase.workflow.service import WorkflowService


class FlakyCodingAdapter(ScriptedCodingAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def execute_task(self, task, session):
        self.calls += 1
        if self.calls == 1:
            root = Path(session.workspace_path)
            root.joinpath("calculator.py").write_text(
                "def evaluate(expression: str):\n    raise RuntimeError('broken')\n",
                encoding="utf-8",
            )
            root.joinpath("test_calculator.py").write_text(_TEST, encoding="utf-8")
            from ase.contracts.domain import AgentRunResult, ExecutionStatus

            return AgentRunResult(
                session_id=session.session_id,
                status=ExecutionStatus.SUCCEEDED,
                summary="wrote a broken implementation",
                events=[{"type": "code_changed", "path": "calculator.py"}],
            )
        return await super().execute_task(task, session)


def _service(settings, coding=None) -> WorkflowService:
    from tests.doubles import GraphSandbox

    ctx = build_context(settings, coding=coding, sandbox=GraphSandbox())
    return WorkflowService(ctx)


def _seed(settings) -> tuple[str, str]:
    ctx = build_context(settings)
    project = Project(name="calculator", repo_path=str(settings.ase_source_root), base_ref="HEAD")
    requirement = Requirement(
        project_id=project.id,
        title="科学计数法",
        statement="为计算器增加一个支持科学计数法的表达式计算功能，并补充必要的单元测试。",
    )
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
        save_requirement(session, requirement)
    return project.id, requirement.id


def test_approval_interrupts_and_resumes(settings) -> None:
    service = _service(settings)
    project_id, requirement_id = _seed(settings)
    # The seed used a different context and sqlite file. Rebuild against the same settings.
    paused = service.start(project_id, requirement_id, "为计算器增加科学计数法表达式计算，并补充单元测试。")
    assert paused["status"] == "waiting_approval"
    assert "human_approval" in paused["next"]
    resumed = service.resume(paused["workflow_id"], "approved", "looks right")
    assert resumed["status"] == "delivered"
    kinds = [event["event_type"] for event in resumed["events"]]
    assert "Approval Requested" in kinds
    assert "Approval Completed" in kinds
    assert "Delivery Completed" in kinds


def test_rejected_approval_stops(settings) -> None:
    service = _service(settings)
    project_id, requirement_id = _seed(settings)
    paused = service.start(project_id, requirement_id, "增加科学计数法")
    resumed = service.resume(paused["workflow_id"], "rejected", "no")
    assert resumed["status"] == "cancelled"


def test_failed_test_enters_diagnosis(settings) -> None:
    coding = FlakyCodingAdapter()
    service = _service(settings, coding=coding)
    project_id, requirement_id = _seed(settings)
    paused = service.start(project_id, requirement_id, "增加科学计数法")
    resumed = service.resume(paused["workflow_id"], "approved", "go")
    kinds = [event["event_type"] for event in resumed["events"]]
    assert "Diagnosis Completed" in kinds
    assert "Repair Completed" in kinds
    assert coding.calls >= 2
    assert resumed["status"] == "delivered"


def test_same_thread_restores_after_a_new_service(settings) -> None:
    first = _service(settings)
    project_id, requirement_id = _seed(settings)
    paused = first.start(project_id, requirement_id, "增加科学计数法")
    second = _service(settings)
    # A new service has a new MemorySaver, so the interrupted thread is gone.
    # The business row is still waiting, which is the database/checkpoint split.
    from ase.db.session import session_scope as scope
    from ase.store import get_workflow

    with scope(second.ctx.sessions) as session:
        row = get_workflow(session, paused["workflow_id"])
    assert row is not None
    assert row.status == "waiting_approval"
    resumed = second.resume(paused["workflow_id"], "approved", "restored checkpointer")
    assert resumed["status"] == "delivered"
