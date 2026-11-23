import asyncio
from pathlib import Path

import pytest

from ase.adapters.openhands import OpenHandsCodingAdapter
from ase.contracts.domain import AgentTaskRequest, ExecutionStatus


pytest.importorskip("openhands.sdk")


def test_real_conversation_with_scripted_llm(settings) -> None:
    from openhands.sdk.llm.message import Message, MessageToolCall, TextContent
    from openhands.sdk.testing import TestLLM

    workspace = settings.workspace_root / "sdk"
    workspace.mkdir(parents=True)
    target = workspace / "sdk_edit.txt"
    llm = TestLLM.from_messages(
        [
            Message(
                role="assistant",
                content=[TextContent(text="create the file")],
                tool_calls=[
                    MessageToolCall(
                        id="call-edit",
                        name="file_editor",
                        arguments='{"command":"create","path":"'
                        + str(target).replace("\\", "\\\\")
                        + '","file_text":"scientific notation"}',
                        origin="completion",
                    )
                ],
            ),
            Message(
                role="assistant",
                content=[TextContent(text="finished")],
                tool_calls=[
                    MessageToolCall(
                        id="call-finish",
                        name="finish",
                        arguments='{"message": "calculator task finished"}',
                        origin="completion",
                    )
                ],
            ),
        ]
    )
    adapter = OpenHandsCodingAdapter(settings, llm=llm)
    session = asyncio.run(adapter.create_session(str(workspace)))
    result = asyncio.run(
        adapter.execute_task(
            AgentTaskRequest(task_id="sdk", instruction="Finish immediately.", workspace_path=str(workspace), timeout_seconds=60),
            session,
        )
    )
    assert result.error is None
    assert target.read_text(encoding="utf-8") == "scientific notation"
    assert result.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED}
    assert result.session_id == session.session_id
    events = asyncio.run(adapter.get_execution_events(session.session_id))
    assert isinstance(events, list)
    asyncio.run(adapter.close_session(session.session_id))
    assert Path(session.workspace_path).is_dir()
