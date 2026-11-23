"""The only ASE module allowed to import openhands.sdk."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
import urllib.request
from pathlib import Path
from typing import Any

os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
for _proxy_name in ("NO_PROXY", "no_proxy"):
    _proxy_parts = [item.strip() for item in os.environ.get(_proxy_name, "").split(",") if item.strip()]
    for _item in ("127.0.0.1", "localhost"):
        if _item not in _proxy_parts:
            _proxy_parts.append(_item)
    os.environ[_proxy_name] = ",".join(_proxy_parts)

from ase.adapters.coding import CodingAgentAdapter
from ase.agents.llm_client import secret_value
from ase.config import Settings
from ase.contracts.domain import (
    AgentRunResult,
    AgentSession,
    AgentTaskRequest,
    ExecutionStatus,
    new_id,
)
from ase.infra.agent_container import AgentContainerManager
from ase.infra.policy import assert_workspace_allowed
from ase.infra.redact import redact

_CONSTRAINTS = """
约束：
- 只读写 /workspace 里的项目文件。
- 可以运行 python -m pytest。
- 不要使用 eval、exec、subprocess、sudo、curl、wget、pip、docker。
- 不要访问 /var/run/docker.sock、ASE 源码或任何密钥文件。
- 不要提交或合并 git 分支。
- 完成后调用 finish。
"""


class OpenHandsCodingAdapter(CodingAgentAdapter):
    def __init__(
        self,
        settings: Settings,
        llm: Any | None = None,
        containers: AgentContainerManager | None = None,
    ) -> None:
        self.settings = settings
        self._llm = llm
        self.containers = containers
        self._conversations: dict[str, Any] = {}
        self._sessions: dict[str, AgentSession] = {}
        self._status: dict[str, str] = {}

    async def create_session(self, workspace_path: str) -> AgentSession:
        path = assert_workspace_allowed(Path(workspace_path), self.settings)
        session = AgentSession(session_id=new_id(), workspace_path=str(path), runtime="openhands")
        self._sessions[session.session_id] = session
        self._status[session.session_id] = "idle"
        return session

    async def execute_task(self, task: AgentTaskRequest, session: AgentSession) -> AgentRunResult:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._execute_sync, task, session),
                timeout=task.timeout_seconds,
            )
        except TimeoutError:
            await self.interrupt(session.session_id)
            return AgentRunResult(
                session_id=session.session_id,
                status=ExecutionStatus.TIMED_OUT,
                summary="openhands session timed out",
                events=await self.get_execution_events(session.session_id),
                error="timeout",
            )
        except Exception as exc:
            self._status[session.session_id] = "error"
            secret = secret_value(self.settings)
            return AgentRunResult(
                session_id=session.session_id,
                status=ExecutionStatus.FAILED,
                summary="openhands session failed",
                events=await self.get_execution_events(session.session_id),
                error=redact(f"{type(exc).__name__}: {exc}", secret),
            )

    def _execute_sync(self, task: AgentTaskRequest, session: AgentSession) -> AgentRunResult:
        from openhands.sdk import Agent, Conversation
        from openhands.sdk.security import ConfirmRisky, PatternSecurityAnalyzer
        from openhands.sdk.security.risk import SecurityRisk
        from openhands.sdk.workspace import LocalWorkspace, RemoteWorkspace

        workspace = assert_workspace_allowed(Path(session.workspace_path), self.settings)
        llm = self._llm or build_llm(self.settings)
        agent = Agent(llm=llm, tools=_coding_tools())
        remote = self._llm is None or bool(task.agent_host)
        if remote:
            if self.containers is None:
                raise RuntimeError("OpenHands remote execution has no container manager")
            handle = self.containers.require_workspace(workspace)
            sdk_workspace = RemoteWorkspace(
                host=handle.host,
                working_dir="/workspace",
                api_key=handle.session_api_key,
            )
        else:
            sdk_workspace = LocalWorkspace(working_dir=str(workspace))
        requested = uuid.UUID(task.conversation_id) if task.conversation_id else None
        conversation = _open_conversation(agent, sdk_workspace, self.settings.openhands_max_iterations, requested)
        self._conversations[session.session_id] = conversation
        self._status[session.session_id] = "running"
        try:
            conversation, status_name = _drive(conversation, task, remote, self, session.session_id)
        except Exception as exc:
            if not remote or not _is_disconnect(exc):
                raise
            _wait_host(handle.host)
            sdk_workspace = RemoteWorkspace(
                host=handle.host,
                working_dir="/workspace",
                api_key=handle.session_api_key,
            )
            conversation = _open_conversation(
                agent,
                sdk_workspace,
                self.settings.openhands_max_iterations,
                uuid.UUID(str(conversation.id)),
            )
            self._conversations[session.session_id] = conversation
            if not _has_user_message(conversation):
                return AgentRunResult(
                    session_id=session.session_id,
                    status=ExecutionStatus.INTERRUPTED,
                    summary="agent server restarted before the conversation was stored",
                    error="interrupted",
                    conversation_id=str(conversation.id),
                )
            conversation, status_name = _drive(conversation, task, remote, self, session.session_id)
        events = _event_payloads(conversation, secret_value(self.settings))
        usage = _usage(conversation)
        if usage:
            events.append({"type": "ModelUsage", "source": "openhands", "text": json.dumps(usage)[:2000]})
        if status_name in {"finished", "idle"}:
            mapped = ExecutionStatus.SUCCEEDED
        elif status_name == "paused":
            mapped = ExecutionStatus.INTERRUPTED
        else:
            mapped = ExecutionStatus.FAILED
        return AgentRunResult(
            session_id=session.session_id,
            status=mapped,
            summary=f"openhands execution_status={status_name}",
            events=events,
            conversation_id=str(conversation.id),
            usage=usage or None,
        )

    async def get_session_status(self, session_id: str) -> str:
        conversation = self._conversations.get(session_id)
        if conversation is None:
            return self._status.get(session_id, "missing")
        return _status_name(conversation)

    async def get_execution_events(self, session_id: str) -> list[dict]:
        conversation = self._conversations.get(session_id)
        if conversation is None:
            return []
        return _event_payloads(conversation, secret_value(self.settings))

    async def interrupt(self, session_id: str) -> None:
        conversation = self._conversations.get(session_id)
        if conversation is not None:
            conversation.interrupt()
        self._status[session_id] = "interrupted"

    async def close_session(self, session_id: str) -> None:
        conversation = self._conversations.get(session_id)
        if conversation is not None:
            conversation.close()
        self._status[session_id] = "closed"


def build_llm(settings: Settings) -> Any:
    from openhands.sdk import LLM

    key = secret_value(settings)
    if not key:
        raise RuntimeError("ASE_LLM_API_KEY is empty")
    model = settings.llm_model
    if settings.llm_provider == "deepseek":
        model = f"openai/{settings.llm_model}"
    extra: dict[str, Any] = {}
    if settings.llm_provider == "deepseek":
        extra = {"thinking": {"type": "disabled"}}
    return LLM(
        model=model,
        api_key=key,
        base_url=settings.llm_api_base,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
        litellm_extra_body=extra,
    )


def _coding_tools() -> list[Any]:
    import openhands.tools  # noqa: F401
    from openhands.sdk import Tool

    return [Tool(name="file_editor"), Tool(name="terminal")]


def _open_conversation(agent: Any, workspace: Any, max_iterations: int, conversation_id: uuid.UUID | None) -> Any:
    from openhands.sdk import Conversation

    return Conversation(
        agent=agent,
        workspace=workspace,
        max_iteration_per_run=max_iterations,
        conversation_id=conversation_id,
    )


def _install_policy(conversation: Any) -> None:
    from openhands.sdk.security import ConfirmRisky, PatternSecurityAnalyzer
    from openhands.sdk.security.risk import SecurityRisk

    base = PatternSecurityAnalyzer()
    analyzer = PatternSecurityAnalyzer(
        high_patterns=list(base.high_patterns)
        + [
            (r"docker\.sock", "Docker socket access", "ase-socket"),
            (r"--privileged", "Privileged mode", "ase-privileged"),
            (r"\bpip(?:3)?\s+install\b", "Unapproved package install", "ase-pip"),
            (r"\b(?:sudo|chmod|chown)\b", "Privilege change", "ase-priv"),
        ]
    )
    conversation.set_security_analyzer(analyzer)
    conversation.set_confirmation_policy(ConfirmRisky(threshold=SecurityRisk.MEDIUM, confirm_unknown=True))


def _drive(conversation: Any, task: AgentTaskRequest, remote: bool, adapter: OpenHandsCodingAdapter, session_id: str) -> tuple[Any, str]:
    status_name = _status_name(conversation)
    if not _has_user_message(conversation):
        if remote:
            _install_policy(conversation)
        conversation.send_message(task.instruction + _CONSTRAINTS)
        status_name = "running"
    paused_resumes = 0
    if status_name != "finished":
        for _ in range(6):
            conversation.run()
            status_name = _status_name(conversation)
            adapter._status[session_id] = status_name
            if status_name == "waiting_for_confirmation":
                conversation.reject_pending_actions("ASE policy denied this tool action")
                continue
            if status_name == "paused" and paused_resumes < 1:
                paused_resumes += 1
                continue
            break
    return conversation, status_name


def _is_disconnect(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = ("connect", "disconnect", "timeout", "refused", "reset", "502", "503", "504", "unavailable")
    return any(marker in text for marker in markers)


def _wait_host(host: str) -> None:
    deadline = time.perf_counter() + 60
    while time.perf_counter() < deadline:
        try:
            with urllib.request.urlopen(host + "/health", timeout=2) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(1)
    raise RuntimeError("agent server did not return after restart")


def _has_user_message(conversation: Any) -> bool:
    events = getattr(getattr(conversation, "state", None), "events", None)
    if events is None:
        return False
    for event in events:
        data: dict[str, Any] = {}
        if hasattr(event, "model_dump"):
            try:
                data = event.model_dump(mode="json")
            except Exception:
                data = {}
        if data.get("source") == "user":
            return True
    return False


def _status_name(conversation: Any) -> str:
    status = conversation.state.execution_status
    return str(status.value if hasattr(status, "value") else status)


def _event_payloads(conversation: Any, secret: str) -> list[dict]:
    events = getattr(conversation.state, "events", None)
    if events is None:
        return []
    payloads: list[dict] = []
    for event in events:
        data: dict[str, Any] = {}
        if hasattr(event, "model_dump"):
            try:
                data = event.model_dump(mode="json")
            except Exception:
                data = {}
        rendered = redact(json.dumps(data, default=str)[:2000], secret)
        payloads.append(
            {
                "type": type(event).__name__,
                "source": "openhands",
                "tool": data.get("tool_name") or data.get("name"),
                "text": rendered,
            }
        )
    return payloads


def _usage(conversation: Any) -> dict[str, Any]:
    stats = getattr(conversation, "conversation_stats", None)
    if stats is None:
        return {}
    for name in ("usage", "metrics", "model_dump"):
        value = getattr(stats, name, None)
        if callable(value):
            try:
                dumped = value()
            except Exception:
                continue
            if isinstance(dumped, dict):
                return dumped
        elif hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
    return {"stats_type": type(stats).__name__}
