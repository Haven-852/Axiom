import asyncio
import time
from typing import Any

from ase.agents.cancellation import CancellationContext
from ase.agents.lifecycle import assert_agent_transition
from ase.agents.registry import AgentRegistry
from ase.agents.schema import validate_payload
from ase.config import Settings
from ase.contracts.domain import (
    AgentContext,
    AgentResult,
    AgentRun,
    AgentRunStatus,
    Artifact,
    ModelUsage,
    new_id,
    utcnow,
)
from ase.db.session import session_scope
from ase.errors import AgentCancelledError, AgentError, AgentExecutionError, AgentTimeoutError
from ase.infra.redact import redact
from ase.store import find_artifact_by_uri, get_agent_run_by_key, save_agent_run, save_artifact, save_model_usage, update_agent_run

_RESERVED = {"workflow_id", "agent_run_id", "id", "project_id"}
_SECRET_KEYS = {"api_key", "authorization", "password", "token", "ase_llm_api_key"}


class AgentRunner:
    def __init__(self, registry: AgentRegistry, sessions, settings: Settings) -> None:
        self.registry = registry
        self.sessions = sessions
        self.settings = settings

    async def run(
        self,
        *,
        workflow_id: str,
        project_id: str,
        agent_name: str,
        input_data: dict,
        task_id: str | None = None,
        input_artifact_ids: list[str] | None = None,
        execution_ids: list[str] | None = None,
        idempotency_key: str | None = None,
        correlation_id: str | None = None,
    ) -> AgentResult:
        existing = None
        if idempotency_key:
            with session_scope(self.sessions) as session:
                existing = get_agent_run_by_key(session, idempotency_key)
        if existing is not None and existing.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.TIMEOUT,
            AgentRunStatus.CANCELLED,
        }:
            return _result_from(existing)
        definition = self.registry.get_definition(agent_name)
        validate_payload(input_data, definition.input_schema)
        snapshot = scrub(input_data, _secret(self.settings))
        if existing is None:
            run = AgentRun(
                workflow_id=workflow_id,
                project_id=project_id,
                task_id=task_id,
                agent_name=definition.agent_id,
                agent_version=definition.version,
                status=AgentRunStatus.PENDING,
                input_artifact_ids=list(input_artifact_ids or []),
                input_snapshot=snapshot,
                idempotency_key=idempotency_key,
            )
            with session_scope(self.sessions) as session:
                save_agent_run(session, run)
        else:
            run = existing
        cancellation = CancellationContext(self.sessions, workflow_id)
        context = AgentContext(
            workflow_id=workflow_id,
            agent_run_id=run.id,
            project_id=project_id,
            task_id=task_id,
            correlation_id=correlation_id or new_id(),
            input_artifact_ids=list(input_artifact_ids or []),
            execution_ids=list(execution_ids or []),
        )
        context.bind_cancellation(cancellation)
        if run.status != AgentRunStatus.RUNNING:
            _move(self.sessions, run, AgentRunStatus.RUNNING, started_at=run.started_at or utcnow())
        agent = self.registry.get(agent_name)
        started = time.perf_counter()
        try:
            await cancellation.raise_if_cancelled()
            result = await asyncio.wait_for(agent.run(context, input_data), timeout=definition.timeout_seconds)
        except AgentCancelledError as exc:
            return _finish(self, run, AgentRunStatus.CANCELLED, {}, error=exc, started=started)
        except TimeoutError as exc:
            timeout = AgentTimeoutError("agent timed out")
            timeout.__cause__ = exc
            return _finish(self, run, AgentRunStatus.TIMEOUT, {}, error=timeout, started=started)
        except AgentError as exc:
            return _finish(self, run, AgentRunStatus.FAILED, {}, error=exc, started=started)
        except Exception as exc:
            return _finish(self, run, AgentRunStatus.FAILED, {}, error=AgentExecutionError(type(exc).__name__), started=started)
        output = scrub({key: value for key, value in result.output.items() if key not in _RESERVED}, _secret(self.settings))
        if result.status != AgentRunStatus.COMPLETED:
            status = result.status if result.status not in {AgentRunStatus.PENDING, AgentRunStatus.RUNNING} else AgentRunStatus.FAILED
            _store_usage(self, run, result, started)
            return _finish(
                self,
                run,
                status,
                output,
                error_message=scrub_text(result.error_message or "", _secret(self.settings)) or None,
                error_code=result.error_code,
                started=started,
                execution_ids=list(result.execution_ids),
            )
        try:
            validate_payload(output, definition.output_schema, output=True)
        except AgentError as exc:
            return _finish(self, run, AgentRunStatus.FAILED, output, error=exc, started=started, execution_ids=result.execution_ids)
        status = result.status if result.status != AgentRunStatus.PENDING else AgentRunStatus.COMPLETED
        if status == AgentRunStatus.RUNNING:
            status = AgentRunStatus.FAILED
        artifact_ids = _store_artifacts(self, run, result)
        _store_usage(self, run, result, started)
        return _finish(
            self,
            run,
            status,
            output,
            error_message=scrub_text(result.error_message or "", _secret(self.settings)) or None,
            error_code=result.error_code,
            started=started,
            execution_ids=list(result.execution_ids),
            output_artifact_ids=artifact_ids,
            metadata=scrub(result.metadata, _secret(self.settings)),
        )


def _move(sessions, run: AgentRun, status: AgentRunStatus, **fields: Any) -> None:
    assert_agent_transition(run.status.value, status.value)
    run.status = status
    for key, value in fields.items():
        setattr(run, key, value)
    run.touch()
    with session_scope(sessions) as session:
        update_agent_run(session, run)


def _finish(
    runner: AgentRunner,
    run: AgentRun,
    status: AgentRunStatus,
    output: dict,
    *,
    error: AgentError | None = None,
    error_message: str | None = None,
    error_code: str | None = None,
    started: float,
    execution_ids: list[str] | None = None,
    output_artifact_ids: list[str] | None = None,
    metadata: dict | None = None,
) -> AgentResult:
    secret = _secret(runner.settings)
    message = error_message if error is None else scrub_text(str(error), secret)
    code = error_code if error is None else getattr(error, "code", "agent_error")
    run.output_snapshot = output
    run.error_code = code if status != AgentRunStatus.COMPLETED else None
    run.error_message = message if status != AgentRunStatus.COMPLETED else None
    run.execution_ids = list(execution_ids or run.execution_ids)
    run.output_artifact_ids = list(output_artifact_ids or [])
    run.completed_at = utcnow()
    _move(runner.sessions, run, status, completed_at=run.completed_at)
    del started
    return AgentResult(
        agent_name=run.agent_name,
        agent_version=run.agent_version,
        workflow_id=run.workflow_id,
        agent_run_id=run.id,
        status=status,
        output=output,
        input_artifact_ids=list(run.input_artifact_ids),
        output_artifact_ids=list(run.output_artifact_ids),
        execution_ids=list(run.execution_ids),
        error_code=run.error_code,
        error_message=run.error_message,
        metadata=metadata or {},
    )


def _store_artifacts(runner: AgentRunner, run: AgentRun, result: AgentResult) -> list[str]:
    created: list[str] = []
    payloads = result.metadata.get("artifacts") if isinstance(result.metadata, dict) else None
    if not payloads:
        return list(result.output_artifact_ids)
    with session_scope(runner.sessions) as session:
        for payload in payloads:
            uri = str(payload.get("uri") or f"agent://{run.id}")
            existing = find_artifact_by_uri(session, run.workflow_id, uri)
            if existing is not None:
                created.append(existing.id)
                continue
            artifact = Artifact(
                project_id=run.project_id,
                workflow_id=run.workflow_id,
                execution_id=(result.execution_ids or [None])[0],
                kind=str(payload.get("kind") or "agent_output"),
                uri=str(payload.get("uri") or f"agent://{run.id}"),
                body=scrub_text(str(payload.get("body") or ""), _secret(runner.settings)),
            )
            save_artifact(session, artifact)
            created.append(artifact.id)
    return created


def _store_usage(runner: AgentRunner, run: AgentRun, result: AgentResult, started: float) -> None:
    usage = result.metadata.get("usage") if isinstance(result.metadata, dict) else None
    if not isinstance(usage, dict):
        return
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if not isinstance(prompt, int) and not isinstance(completion, int):
        return
    latency = usage.get("latency_seconds")
    if not isinstance(latency, (int, float)):
        latency = round(time.perf_counter() - started, 3)
    cost = usage.get("estimated_cost")
    with session_scope(runner.sessions) as session:
        save_model_usage(
            session,
            ModelUsage(
                project_id=run.project_id,
                workflow_id=run.workflow_id,
                task_id=run.task_id,
                execution_id=(result.execution_ids or [None])[0],
                provider=str(usage.get("provider") or runner.settings.llm_provider),
                model=str(usage.get("model") or runner.settings.llm_model),
                prompt_tokens=prompt if isinstance(prompt, int) else None,
                completion_tokens=completion if isinstance(completion, int) else None,
                total_tokens=(prompt or 0) + (completion or 0) if isinstance(prompt, int) and isinstance(completion, int) else None,
                latency_seconds=float(latency),
                estimated_cost=float(cost) if isinstance(cost, (int, float)) else None,
                agent_name=run.agent_name,
                agent_run_id=run.id,
            ),
        )


def _result_from(run: AgentRun) -> AgentResult:
    return AgentResult(
        agent_name=run.agent_name,
        agent_version=run.agent_version,
        workflow_id=run.workflow_id,
        agent_run_id=run.id,
        status=run.status,
        output=dict(run.output_snapshot),
        input_artifact_ids=list(run.input_artifact_ids),
        output_artifact_ids=list(run.output_artifact_ids),
        execution_ids=list(run.execution_ids),
        error_code=run.error_code,
        error_message=run.error_message,
    )


def scrub(value, secret: str):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if str(key).lower() in _SECRET_KEYS:
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = scrub(item, secret)
        return cleaned
    if isinstance(value, list):
        return [scrub(item, secret) for item in value]
    if isinstance(value, str):
        return scrub_text(value, secret)
    return value


def scrub_text(value: str, secret: str) -> str:
    return redact(value, secret)


def _secret(settings: Settings) -> str:
    raw = settings.llm_api_key
    return raw.get_secret_value() if hasattr(raw, "get_secret_value") else str(raw)
