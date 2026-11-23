"""Read-only Research Agent. It calls the ASE model client, not OpenHands."""

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ase.agents.base import BaseAgent
from ase.agents.llm_client import complete
from ase.agents.research.prompts import repair_prompt, system_prompt, user_prompt
from ase.agents.research.reader import ProjectReader
from ase.agents.research.schemas import ResearchAssessment, ResearchReport
from ase.agents.research.services import collect_evidence, research_options
from ase.config import Settings
from ase.contracts.domain import AgentContext, AgentResult, AgentRunStatus, AgentSpec
from ase.db.session import session_scope
from ase.errors import ProjectReadError
from ase.store import get_project

_FORMAT_REPAIRS = 2
_SCHEMA_VERSION = "1.0"


def research_definition() -> AgentSpec:
    return AgentSpec(
        agent_id="research",
        name="Research Agent",
        agent_type="research",
        description="Reads a repository and returns a structured research report.",
        input_schema={
            "type": "object",
            "required": ["requirement"],
            "properties": {
                "requirement": {"type": "string"},
                "include_tests": {"type": "boolean"},
                "include_dependencies": {"type": "boolean"},
                "max_files": {"type": "integer"},
                "max_context_chars": {"type": "integer"},
            },
        },
        output_schema={
            "type": "object",
            "required": ["schema_version", "research_summary", "project_analysis", "requirement_analysis", "code_findings"],
            "properties": {
                "schema_version": {"type": "string"},
                "research_summary": {"type": "string"},
                "project_analysis": {"type": "object"},
                "requirement_analysis": {"type": "object"},
                "code_findings": {"type": "array"},
            },
        },
        runtime="ase.llm",
        model="configured",
        tool_permissions=["read_file", "search_code"],
        timeout_seconds=600,
        retry_limit=0,
        version="1",
        capabilities=["requirement-analysis", "project-analysis", "code-search"],
        max_retries=_FORMAT_REPAIRS,
    )


class ResearchAgent(BaseAgent):
    def __init__(self, settings: Settings, sessions, complete_fn=None) -> None:
        self.settings = settings
        self.sessions = sessions
        self.complete_fn = complete_fn or complete

    @property
    def name(self) -> str:
        return "research"

    @property
    def version(self) -> str:
        return "1"

    async def run(self, context: AgentContext, input_data: dict) -> AgentResult:
        await context.raise_if_cancelled()
        try:
            reader = self._reader(context.project_id, input_data)
            evidence = await collect_evidence(reader, context.project_id, input_data["requirement"], research_options(input_data))
            assessment, usage = await self._assess(input_data["requirement"], evidence["evidence_text"])
            findings, notes = await self._verify(reader, context.project_id, assessment.code_findings)
            report = self._report(input_data["requirement"], evidence, assessment, findings, notes)
        except (ProjectReadError, ValidationError, RuntimeError, json.JSONDecodeError) as exc:
            return self._failed(context, exc)
        body = json.dumps(
            {
                "schema_version": report.schema_version,
                "agent_run_id": context.agent_run_id,
                "workflow_id": context.workflow_id,
                "project_id": context.project_id,
                "revision": evidence["revision"],
                "files_read": evidence["files_read"],
                "search_results": evidence["search_results"],
                "content": report.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
        metadata: dict[str, Any] = {
            "artifacts": [
                {
                    "kind": "research_report",
                    "uri": f"research://{context.agent_run_id}",
                    "body": body,
                }
            ],
            "files_read": evidence["files_read"],
            "search_results": evidence["search_results"],
            "revision": evidence["revision"],
        }
        if usage is not None:
            metadata["usage"] = usage
        return AgentResult(
            agent_name=self.name,
            agent_version=self.version,
            workflow_id=context.workflow_id,
            agent_run_id=context.agent_run_id,
            status=AgentRunStatus.COMPLETED,
            output=report.model_dump(mode="json"),
            metadata=metadata,
        )

    def _reader(self, project_id: str, input_data: dict) -> ProjectReader:
        with session_scope(self.sessions) as session:
            project = get_project(session, project_id)
        if project is None:
            raise ProjectReadError("project_not_found", "project does not exist")
        options = research_options(input_data)
        return ProjectReader(project_id, Path(project.repo_path), max_files=options["max_files"])

    async def _assess(self, requirement: str, evidence: str) -> tuple[ResearchAssessment, dict | None]:
        prompt = user_prompt(requirement, evidence)
        text, usage = await self._call(system_prompt(), prompt, json_mode=True)
        error = ""
        for _ in range(_FORMAT_REPAIRS + 1):
            try:
                return ResearchAssessment.model_validate(_parse(text)), usage
            except (ValidationError, json.JSONDecodeError) as exc:
                error = str(exc)
                if _ == _FORMAT_REPAIRS:
                    raise
                text, extra = await self._call(system_prompt(), repair_prompt(text, error), json_mode=True)
                usage = _add_usage(usage, extra)
        raise RuntimeError(error or "research report was not valid JSON")

    async def _call(self, system: str, user: str, *, json_mode: bool) -> tuple[str, dict | None]:
        try:
            text, log = await asyncio.to_thread(self.complete_fn, self.settings, system, user, json_mode)
        except TypeError:
            text, log = await asyncio.to_thread(self.complete_fn, self.settings, system, user)
        except RuntimeError as exc:
            if json_mode and "model HTTP 400" in str(exc):
                text, log = await asyncio.to_thread(self.complete_fn, self.settings, system, user, False)
            else:
                raise
        return text, _usage_from(log, self.settings)

    async def _verify(self, reader: ProjectReader, project_id: str, findings) -> tuple[list, list[str]]:
        kept = []
        notes = []
        for finding in findings:
            try:
                loaded = await reader.read_file(project_id, finding.file_path)
            except ProjectReadError:
                notes.append(f"已丢弃无法在仓库中核验的发现 {finding.file_path}")
                continue
            located = _locate_line(loaded["content"].splitlines(), finding.evidence)
            if located is None:
                notes.append(f"已丢弃证据无法在文件中定位的发现 {finding.file_path}")
                continue
            kept.append(finding.model_copy(update={"start_line": located, "end_line": located}))
        return kept, notes

    def _report(self, requirement: str, evidence: dict, assessment: ResearchAssessment, findings, notes: list[str]) -> ResearchReport:
        analysis = assessment.requirement_analysis
        if not analysis.objectives:
            analysis.objectives = [requirement.strip()[:500]]
        questions = list(analysis.clarification_questions)
        if len(requirement.strip()) < 8 and "需求过短，无法确定具体行为。" not in questions:
            questions.append("需求过短，无法确定具体行为。")
        limitations = list(dict.fromkeys([*evidence["limitations"], *assessment.limitations, *notes]))
        return ResearchReport(
            schema_version=_SCHEMA_VERSION,
            requirement_analysis=analysis.model_copy(update={"clarification_questions": questions[:8]}),
            project_analysis=evidence["project_analysis"],
            code_findings=findings,
            existing_capabilities=assessment.existing_capabilities,
            missing_capabilities=assessment.missing_capabilities,
            technical_recommendations=assessment.technical_recommendations,
            technical_risks=assessment.technical_risks,
            acceptance_criteria=assessment.acceptance_criteria,
            clarification_questions=questions[:8],
            research_summary=assessment.research_summary,
            limitations=limitations[:16],
        )

    def _failed(self, context: AgentContext, exc: Exception) -> AgentResult:
        message = str(exc)
        secret = self.settings.llm_api_key.get_secret_value()
        if secret:
            message = message.replace(secret, "[redacted]")
        code = getattr(exc, "code", None) or "agent_execution_failed"
        return AgentResult(
            agent_name=self.name,
            agent_version=self.version,
            workflow_id=context.workflow_id,
            agent_run_id=context.agent_run_id,
            status=AgentRunStatus.FAILED,
            error_code=str(code)[:64],
            error_message=message[:500],
        )


def _locate_line(lines: list[str], evidence: str) -> int | None:
    text = evidence.strip()
    if not text:
        return None
    for number, line in enumerate(lines, start=1):
        if text in line:
            return number
    window = 24 if len(text) >= 24 else len(text)
    if window < 8:
        return None
    for start in range(0, len(text) - window + 1, 8):
        piece = text[start : start + window]
        for number, line in enumerate(lines, start=1):
            if piece in line:
                return number
    return None


def _parse(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise json.JSONDecodeError("object required", raw, 0)
    return data


def _usage_from(log: dict, settings: Settings) -> dict | None:
    raw = log.get("usage") if isinstance(log, dict) else None
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_tokens")
    completion = raw.get("completion_tokens")
    if isinstance(prompt, bool) or isinstance(completion, bool):
        return None
    if not isinstance(prompt, int) and not isinstance(completion, int):
        return None
    cost = raw.get("estimated_cost")
    usage = {
        "provider": log.get("provider") or settings.llm_provider,
        "model": log.get("model") or settings.llm_model,
        "prompt_tokens": prompt if isinstance(prompt, int) else None,
        "completion_tokens": completion if isinstance(completion, int) else None,
        "latency_seconds": log.get("elapsed_seconds") if isinstance(log.get("elapsed_seconds"), (int, float)) else None,
    }
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        usage["estimated_cost"] = float(cost)
    return usage


def _add_usage(left: dict | None, right: dict | None) -> dict | None:
    if left is None:
        return right
    if right is None:
        return left
    prompt = _sum_int(left.get("prompt_tokens"), right.get("prompt_tokens"))
    completion = _sum_int(left.get("completion_tokens"), right.get("completion_tokens"))
    latency = _sum_float(left.get("latency_seconds"), right.get("latency_seconds"))
    merged = dict(left)
    merged["prompt_tokens"] = prompt
    merged["completion_tokens"] = completion
    merged["latency_seconds"] = latency
    return merged


def _sum_int(left: object, right: object) -> int | None:
    if isinstance(left, int) and not isinstance(left, bool) and isinstance(right, int) and not isinstance(right, bool):
        return left + right
    if isinstance(left, int) and not isinstance(left, bool):
        return left
    if isinstance(right, int) and not isinstance(right, bool):
        return right
    return None


def _sum_float(left: object, right: object) -> float | None:
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return float(left) + float(right)
    return None
