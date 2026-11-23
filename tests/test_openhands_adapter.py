import asyncio

from ase.adapters.openhands import OpenHandsCodingAdapter
from ase.contracts.domain import AgentTaskRequest, ExecutionStatus


def test_adapter_records_execution_failure(settings, tmp_path) -> None:
    workspace = settings.workspace_root / "ws"
    workspace.mkdir(parents=True)
    adapter = OpenHandsCodingAdapter(settings)

    def explode(task, session):
        raise RuntimeError("boom")

    adapter._execute_sync = explode
    session = asyncio.run(adapter.create_session(str(workspace)))
    result = asyncio.run(
        adapter.execute_task(
            AgentTaskRequest(task_id="t", instruction="edit", workspace_path=str(workspace)),
            session,
        )
    )
    assert result.status is ExecutionStatus.FAILED
    assert "boom" in (result.error or "")
    asyncio.run(adapter.interrupt(session.session_id))
    assert asyncio.run(adapter.get_session_status(session.session_id)) == "interrupted"


def test_graph_does_not_import_openhands() -> None:
    source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "src" / "ase" / "workflow" / "graph.py").read_text(
        encoding="utf-8"
    )
    assert "import openhands" not in source
    assert "from openhands" not in source
