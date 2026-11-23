"""Coding task path through the real OpenHands SDK. The model is the SDK test double."""

import asyncio
import json
import subprocess

import pytest

from ase.adapters.openhands import OpenHandsCodingAdapter
from ase.agents.coding_agent import CodingAgent, coding_definition
from ase.agents.registry import AgentRegistry
from ase.agents.runner import AgentRunner
from ase.app import build_context
from ase.contracts.domain import ExecutionStatus
from ase.db.session import session_scope
from ase.execution.coding_service import CodingTaskService
from ase.store import get_execution
from tests.test_phase24 import _approved, _workspace

pytest.importorskip("openhands.sdk")


def test_approved_task_uses_openhands_sdk(settings) -> None:
    from openhands.sdk.llm.message import Message, MessageToolCall, TextContent
    from openhands.sdk.testing import TestLLM

    ctx, _service, workflow_id, task_id, _plan = _approved(settings)
    workspace = _workspace(settings)
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
                        arguments=json.dumps({"command": "create", "path": str(target), "file_text": "scientific notation"}),
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
                        arguments=json.dumps({"message": "done"}),
                        origin="completion",
                    )
                ],
            ),
        ]
    )
    adapter = OpenHandsCodingAdapter(settings, llm=llm)
    registry = AgentRegistry()
    registry.register(coding_definition(), lambda: CodingAgent(adapter, ctx.sessions))
    service = CodingTaskService(ctx, AgentRunner(registry, ctx.sessions, settings))
    started = service.start_task(workflow_id, task_id, str(workspace))
    assert target.read_text(encoding="utf-8") == "scientific notation"
    with session_scope(ctx.sessions) as session:
        execution = get_execution(session, started["execution_id"])
    assert execution.conversation_id
    assert execution.status == ExecutionStatus.COMPLETED
    repeated = service.start_task(workflow_id, task_id, str(workspace))
    assert repeated["execution_id"] == started["execution_id"]
    subprocess.run(["git", "status", "--porcelain"], cwd=workspace, check=True, capture_output=True, text=True)
    asyncio.run(adapter.close_session(execution.session_id)) if execution.session_id else None
