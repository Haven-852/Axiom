"""Planning agent. It consumes a research report and does not read the repository."""

import asyncio
import json
import uuid
from typing import Any

from pydantic import ValidationError

from ase.agents.base import BaseAgent
from ase.agents.llm_client import complete
from ase.agents.research.schemas import ResearchReport
from ase.config import Settings
from ase.contracts.domain import AgentContext, AgentResult, AgentRunStatus, AgentSpec
from ase.db.session import session_scope
from ase.planning.dag_validator import blocked_task_ids, finalize_plan
from ase.planning.research_input import PlanningInput, load_planning_input, planning_key
from ase.planning.schemas import ClarificationItem, ClarificationStatus, Plan, PlanningDraft, PlanTask
from ase.planning.task_builder import persist_plan

_REPAIRS = 2
_CODE_TYPES = {"implementation", "testing", "refactoring"}


def planning_definition() -> AgentSpec:
    return AgentSpec(
        agent_id="planning",
        name="Planning Agent",
        agent_type="planning",
        description="Turns a research report into a validated task DAG.",
        input_schema={
            "type": "object",
            "required": ["research_artifact_id"],
            "properties": {"research_artifact_id": {"type": "string"}},
        },
        output_schema={
            "type": "object",
            "required": ["schema_version", "plan_id", "tasks", "validation_result"],
            "properties": {
                "schema_version": {"type": "string"},
                "plan_id": {"type": "string"},
                "tasks": {"type": "array"},
                "validation_result": {"type": "object"},
            },
        },
        runtime="ase.llm",
        model="configured",
        tool_permissions=[],
        timeout_seconds=600,
        retry_limit=0,
        version="1",
        capabilities=["task-dag"],
        max_retries=_REPAIRS,
    )


class PlanningAgent(BaseAgent):
    def __init__(self, settings: Settings, sessions, complete_fn=None) -> None:
        self.settings = settings
        self.sessions = sessions
        self.complete_fn = complete_fn or complete

    @property
    def name(self) -> str:
        return "planning"

    @property
    def version(self) -> str:
        return "1"

    async def run(self, context: AgentContext, input_data: dict) -> AgentResult:
        await context.raise_if_cancelled()
        try:
            with session_scope(self.sessions) as session:
                loaded = load_planning_input(session, context.workflow_id, input_data["research_artifact_id"])
        except Exception as exc:
            code = getattr(exc, "code", "research_input_invalid")
            return self._failed(context, code, exc)
        try:
            sealed, usage = await self._plan(loaded, input_data)
            persist_plan(
                self.sessions,
                loaded.requirement_id,
                sealed,
                hold_for_approval=bool(_resolutions(input_data)),
            )
        except _PlanError as exc:
            return self._failed(context, "plan_invalid", exc, usage=exc.usage)
        except (ValidationError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
            return self._failed(context, "plan_invalid", exc)
        body = json.dumps(
            {
                "schema_version": sealed.schema_version,
                "planning_agent_run_id": context.agent_run_id,
                "workflow_id": context.workflow_id,
                "research_artifact_id": loaded.research_artifact_id,
                "research_agent_run_id": loaded.research_agent_run_id,
                "task_ids": [task.task_id for task in sealed.tasks],
                "blocked_task_ids": blocked_task_ids(sealed),
                "content": sealed.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
        metadata: dict[str, Any] = {
            "artifacts": [{"kind": "plan", "uri": f"plan://{context.agent_run_id}", "body": body}],
        }
        if usage is not None:
            metadata["usage"] = usage
        return AgentResult(
            agent_name=self.name,
            agent_version=self.version,
            workflow_id=context.workflow_id,
            agent_run_id=context.agent_run_id,
            status=AgentRunStatus.COMPLETED,
            output=sealed.model_dump(mode="json"),
            metadata=metadata,
        )

    async def _plan(self, loaded: PlanningInput, input_data: dict) -> tuple[Plan, dict | None]:
        resolutions = _resolutions(input_data)
        key = planning_key(
            loaded,
            agent_version=self.version,
            clarification_revision=str(input_data.get("clarification_revision") or ""),
            parent_plan_artifact_id=str(input_data.get("parent_plan_artifact_id") or ""),
            user_change_hash=str(input_data.get("user_change_hash") or ""),
        )
        prompt = _user_prompt(loaded, resolutions)
        text, usage, finish = await self._call(_system_prompt(loaded.research_report, bool(resolutions)), prompt, json_mode=True)
        error = ""
        for attempt in range(_REPAIRS + 1):
            try:
                draft = PlanningDraft.model_validate(_normalize_draft(_parse(text)))
                plan = _assemble(draft, loaded, key)
                if resolutions or input_data.get("parent_clarifications") or input_data.get("deferred_questions"):
                    plan = _apply_user_decisions(
                        plan,
                        resolutions,
                        list(input_data.get("parent_clarifications") or []),
                        list(input_data.get("parent_tasks") or []),
                        set(input_data.get("deferred_questions") or []),
                    )
                sealed, result = finalize_plan(
                    plan,
                    loaded.research_report,
                    resolutions or None,
                    set(input_data.get("deferred_questions") or []) or None,
                )
                if result.valid:
                    return sealed, usage
                error = "; ".join(result.errors)
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                error = str(exc)
                if finish == "length":
                    error = "response truncated at max_tokens; return at most 4 short tasks. " + error
            if attempt == _REPAIRS:
                raise _PlanError(error or "plan was not valid", usage)
            text, extra, finish = await self._call(
                _system_prompt(loaded.research_report, bool(resolutions)),
                _repair_prompt(text, error),
                json_mode=True,
            )
            usage = _add_usage(usage, extra)
            if finish == "length":
                error = "the response hit max_tokens and was truncated; return at most 4 short tasks"
        raise ValueError(error or "plan was not valid")

    async def _call(self, system: str, user: str, *, json_mode: bool) -> tuple[str, dict | None, str | None]:
        try:
            text, log = await asyncio.to_thread(self.complete_fn, self.settings, system, user, json_mode)
        except TypeError:
            text, log = await asyncio.to_thread(self.complete_fn, self.settings, system, user)
        except RuntimeError as exc:
            if json_mode and "model HTTP 400" in str(exc):
                text, log = await asyncio.to_thread(self.complete_fn, self.settings, system, user, False)
            else:
                raise
        finish = log.get("finish_reason") if isinstance(log, dict) else None
        return text, _usage_from(log, self.settings), finish if isinstance(finish, str) else None

    def _failed(self, context: AgentContext, code: str, exc: Exception, usage: dict | None = None) -> AgentResult:
        message = str(exc)
        secret = self.settings.llm_api_key.get_secret_value()
        if secret:
            message = message.replace(secret, "[redacted]")
        metadata = {"usage": usage} if usage else {}
        return AgentResult(
            agent_name=self.name,
            agent_version=self.version,
            workflow_id=context.workflow_id,
            agent_run_id=context.agent_run_id,
            status=AgentRunStatus.FAILED,
            error_code=code[:64],
            error_message=message[:2000],
            metadata=metadata,
        )


def _assemble(draft: PlanningDraft, loaded: PlanningInput, key: str) -> Plan:
    plan_id = _stable(f"plan:{key}")
    id_map = {task.task_id: _stable(f"{plan_id}:{task.task_id}") for task in draft.tasks}
    tasks = [_remap_task(task, id_map) for task in draft.tasks]
    report = loaded.research_report
    research_items = [
        ClarificationItem(
            clarification_id=_stable(f"{plan_id}:question:{question}"),
            question=question,
            context=report.requirement_analysis.summary[:1000],
            impact="这个问题会改变实现范围或验收标准。",
            blocking=True,
            affected_task_ids=[task.task_id for task in tasks if task.task_type.value in _CODE_TYPES][:12],
            status=ClarificationStatus.UNRESOLVED,
            source="research_report",
        )
        for question in report.clarification_questions
    ]
    known_questions = {item.question for item in research_items}
    model_items = [
        _remap_clarification(item, plan_id, id_map)
        for item in draft.clarification_items
        if item.question not in known_questions
    ]
    clarifications = (research_items + model_items)[:12]
    open_ids = [item.clarification_id for item in clarifications if item.blocking and item.status == ClarificationStatus.UNRESOLVED]
    rewritten = []
    for task in tasks:
        notes = []
        blocking = list(task.blocking_clarification_ids)
        if report.clarification_questions and task.task_type.value in _CODE_TYPES:
            for clarification_id in open_ids:
                if clarification_id not in blocking:
                    blocking.append(clarification_id)
            notes.append("范围尚未确认，不能把字面量或完整表达式写成已确认需求。")
        for criterion_id in task.source_acceptance_criteria:
            source = next((item.source.value for item in report.acceptance_criteria if item.criterion_id == criterion_id), "")
            note = f"验收标准 {criterion_id} 来自推断，不是已确认需求。"
            if source == "inferred":
                notes.append(note)
        assumptions = notes + [item for item in task.assumptions if item not in notes]
        rewritten.append(task.model_copy(update={"blocking_clarification_ids": blocking[:8], "assumptions": assumptions[:8]}))
    tasks = rewritten
    if report.clarification_questions:
        for item in clarifications:
            item.affected_task_ids = [task.task_id for task in tasks if item.clarification_id in task.blocking_clarification_ids][:12]
            item.status = ClarificationStatus.UNRESOLVED
            item.blocking = True
    placeholder = Plan(
        schema_version="1.0",
        plan_id=plan_id,
        source_research_artifact_id=loaded.research_artifact_id,
        source_research_agent_run_id=loaded.research_agent_run_id,
        requirement_summary=report.requirement_analysis.summary,
        planning_summary=draft.planning_summary,
        tasks=tasks,
        clarification_items=clarifications[:12],
        execution_order=[],
        validation_result=_empty_validation(len(tasks)),
        assumptions=draft.assumptions,
        limitations=_limitations(report, draft),
        risks=[
            risk.model_copy(
                update={
                    "risk_id": _stable(f"{plan_id}:risk:{risk.risk_id}"),
                    "affected_task_ids": [id_map[item] for item in risk.affected_task_ids if item in id_map][:12],
                }
            )
            for risk in draft.risks
        ],
    )
    return placeholder


def _remap_task(task: PlanTask, id_map: dict[str, str]) -> PlanTask:
    return task.model_copy(
        update={
            "task_id": id_map[task.task_id],
            "dependencies": [id_map.get(item, item) for item in task.dependencies],
            "blocking_clarification_ids": [],
        }
    )


def _remap_clarification(item: ClarificationItem, plan_id: str, id_map: dict[str, str]) -> ClarificationItem:
    return item.model_copy(
        update={
            "clarification_id": _stable(f"{plan_id}:question:{item.question}"),
            "affected_task_ids": [id_map[task_id] for task_id in item.affected_task_ids if task_id in id_map],
            "status": ClarificationStatus.UNRESOLVED,
        }
    )


def _limitations(report: ResearchReport, draft: PlanningDraft) -> list[str]:
    merged = list(dict.fromkeys([*report.limitations, *draft.limitations, "未确认的澄清问题保持 unresolved。"]))
    return merged[:16]


def _empty_validation(count: int):
    from ase.planning.schemas import PlanValidationResult

    return PlanValidationResult(
        valid=False,
        structurally_valid=False,
        errors=[],
        warnings=[],
        task_count=count,
        blocked_task_count=0,
        executable_task_count=0,
        topological_order=[],
    )


def _resolutions(input_data: dict) -> dict[str, str]:
    found = {}
    for item in input_data.get("decisions") or []:
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if question and answer:
            found[question] = answer
    return found


def _apply_user_decisions(
    plan: Plan,
    resolutions: dict[str, str],
    parent_clarifications: list[dict],
    parent_tasks: list[dict],
    deferred: set[str] | None = None,
) -> Plan:
    items: list[ClarificationItem] = []
    seen: set[str] = set()
    for item in plan.clarification_items:
        seen.add(item.question)
        items.append(_with_decision(item, resolutions, deferred or set()))
    for raw in parent_clarifications:
        question = str(raw.get("question") or "").strip()
        if not question or question in seen:
            continue
        seen.add(question)
        items.append(
            _with_decision(
                ClarificationItem(
                    clarification_id=str(raw.get("clarification_id") or _stable(f"parent:{question}"))[:40],
                    question=question[:500],
                    context=str(raw.get("context") or question)[:1000],
                    impact=str(raw.get("impact") or "这个问题仍会改变实现范围。")[:500],
                    blocking=True,
                    affected_task_ids=[],
                    status=ClarificationStatus.UNRESOLVED,
                    source=str(raw.get("source") or "planning")[:64],
                    source_reference=str(raw.get("clarification_id") or "")[:200],
                ),
                resolutions,
                deferred or set(),
            )
        )
    unresolved = [item.clarification_id for item in items if item.blocking and item.status == ClarificationStatus.UNRESOLVED]
    titles = {task.title: task.task_id for task in plan.tasks}
    tasks = []
    for task in plan.tasks:
        blocking = unresolved[:8] if task.task_type.value in _CODE_TYPES else []
        assumptions = list(task.assumptions)
        if task.task_type.value in _CODE_TYPES and not any("用户决策" in note for note in assumptions):
            assumptions.insert(0, "已回答的范围来自用户决策，不是研究报告推断。")
        tasks.append(task.model_copy(update={"blocking_clarification_ids": blocking, "assumptions": assumptions[:8]}))
    old_to_new = {str(raw.get("task_id") or ""): titles[str(raw.get("title") or "").strip()] for raw in parent_tasks if str(raw.get("title") or "").strip() in titles}
    retained: list[tuple[dict, str]] = []
    for raw in parent_tasks:
        title = str(raw.get("title") or "").strip()
        if not title or title in titles:
            continue
        titles[title] = _stable(f"{plan.plan_id}:retained:{title}")[:40]
        old_to_new[str(raw.get("task_id") or "")] = titles[title]
        retained.append((raw, titles[title]))
    for raw, task_id in retained:
        dependencies = [
            old_to_new[item]
            for item in raw.get("dependencies") or []
            if item in old_to_new and old_to_new[item] != task_id
        ]
        tasks.append(_retained_task(plan.plan_id, raw, unresolved, dependencies))
    return plan.model_copy(update={"clarification_items": items[:12], "tasks": tasks[:20], "execution_order": []})


def _with_decision(item: ClarificationItem, resolutions: dict[str, str], deferred: set[str] | None = None) -> ClarificationItem:
    answer = resolutions.get(item.question, "")
    if not answer and item.question in (deferred or set()):
        return item.model_copy(update={"status": ClarificationStatus.DEFERRED, "blocking": True, "answer": "", "answered_by": ""})
    if not answer:
        return item.model_copy(update={"status": ClarificationStatus.UNRESOLVED, "answer": "", "answered_by": ""})
    return item.model_copy(
        update={
            "status": ClarificationStatus.ANSWERED,
            "blocking": False,
            "answer": answer[:2000],
            "answered_by": "api-key",
            "source_reference": item.source_reference or item.clarification_id,
        }
    )


def _retained_task(plan_id: str, raw: dict, unresolved: list[str], dependencies: list[str] | None = None) -> PlanTask:
    task_type = str(raw.get("task_type") or "investigation")
    if task_type not in {"implementation", "testing", "refactoring", "documentation", "investigation"}:
        task_type = "investigation"
    assumptions = list(raw.get("assumptions") or [])
    assumptions.append("该任务从上一版保留，模型没有给出删除理由。")
    return PlanTask(
        task_id=_stable(f"{plan_id}:retained:{raw.get('title')}")[:40],
        title=str(raw["title"])[:200],
        description=str(raw.get("description") or raw["title"])[:2000],
        task_type=task_type,
        priority=raw.get("priority") or "medium",
        dependencies=list(dependencies or []),
        related_files=list(raw.get("related_files") or [])[:12],
        acceptance_criteria=list(raw.get("acceptance_criteria") or ["保留上一版任务，直到用户确认删除。"])[:8],
        estimated_complexity=raw.get("estimated_complexity") or "unknown",
        source_findings=list(raw.get("source_findings") or [])[:8],
        source_acceptance_criteria=list(raw.get("source_acceptance_criteria") or [])[:8],
        assumptions=assumptions[:8],
        blocking_clarification_ids=unresolved[:8] if task_type in _CODE_TYPES else [],
    )


def _system_prompt(report: ResearchReport, has_decisions: bool = False) -> str:
    finding_ids = ", ".join(item.finding_id for item in report.code_findings) or "无"
    criterion_ids = ", ".join(f"{item.criterion_id}:{item.source.value}" for item in report.acceptance_criteria) or "无"
    files = ", ".join(sorted({item.file_path for item in report.code_findings})) or "无"
    return (
        "你是 ASE 的 Planning Agent。只返回 JSON，不要 Markdown。\n"
        "不要读取仓库，不要编造文件、行号、finding id 或验收标准 id。\n"
        "source=inferred 的验收标准必须在 assumptions 中写明是推断。\n"
        "clarification status 只能是 unresolved。不要替用户回答未确认问题。\n"
        "不要把科学计数法写成已经确认的完整表达式或已经确认的字面量。\n"
        "implementation、testing、refactoring 会因未确认问题被阻塞。\n"
        "documentation 或 investigation 只有在不选择语法方案时才能不阻塞。\n"
        f"可用 finding id: {finding_ids}\n"
        f"可用 criterion id: {criterion_ids}\n"
        f"可用相对路径: {files}\n"
        "JSON 字段: planning_summary, tasks, clarification_items, risks, assumptions, limitations。\n"
        "task_type 只能是 implementation, testing, refactoring, documentation, investigation。\n"
        "priority 只能是 critical, high, medium, low。\n"
        "estimated_complexity 只能是 small, medium, large, unknown。\n"
        "clarification status 只能是 unresolved。\n"
        "每个 task 必须包含 task_id, title, description, task_type, priority, dependencies, related_files, "
        "acceptance_criteria, estimated_complexity, source_findings, source_acceptance_criteria, assumptions, "
        "blocking_clarification_ids。\n"
        "related_files、source_findings、source_acceptance_criteria 只能使用上面列出的值。\n"
        "最多 4 个任务。title、description、acceptance_criteria 每项不超过 80 个汉字。\n"
        "必须返回完整 JSON，不要在中途截断。\n"
        + (
            "用户决策是新增约束。原样保留回答，不要改写，不要只选择其中一种行为。\n"
            "不要改写 ResearchReport，不要把未出现在用户决策中的问题标成 answered。\n"
            "用户没有回答的问题继续 blocking。不要因为两个问题相近就合并。\n"
            if has_decisions
            else ""
        )
    )


def _user_prompt(loaded: PlanningInput, resolutions: dict[str, str] | None = None) -> str:
    report = "研究报告 JSON:\n" + json.dumps(loaded.research_report.model_dump(mode="json"), ensure_ascii=False)
    if not resolutions:
        return report
    decisions = [{"question": question, "answer": answer, "source": "user"} for question, answer in resolutions.items()]
    return report + "\n\n用户已经明确做出的决策 JSON。这些不是推断:\n" + json.dumps(decisions, ensure_ascii=False)


def _repair_prompt(previous: str, error: str) -> str:
    return f"上次计划没有通过校验。只返回修正后的 JSON。\n错误:\n{error[:1500]}\n\n上次输出:\n{previous[:6000]}"


class _PlanError(Exception):
    def __init__(self, message: str, usage: dict | None) -> None:
        super().__init__(message)
        self.usage = usage


def _normalize_draft(data: dict) -> dict:
    if isinstance(data.get("plan"), dict) and "tasks" not in data:
        data = data["plan"]
    tasks = data.get("tasks") or data.get("task_list") or []
    normalized_tasks = []
    id_map: dict[str, str] = {}
    for index, task in enumerate(tasks if isinstance(tasks, list) else []):
        if not isinstance(task, dict):
            continue
        raw_id = str(task.get("task_id") or task.get("id") or f"t{index + 1}")
        task_id = raw_id if len(raw_id) <= 40 else _stable(raw_id)
        id_map[raw_id] = task_id
        normalized_tasks.append(task)
    rewritten_tasks = []
    for index, task in enumerate(normalized_tasks):
        raw_id = str(task.get("task_id") or task.get("id") or f"t{index + 1}")
        complexity = str(task.get("estimated_complexity") or task.get("complexity") or "unknown").lower()
        priority = str(task.get("priority") or "medium").lower()
        task_type = str(task.get("task_type") or task.get("type") or "investigation").lower()
        rewritten_tasks.append(
            {
                "task_id": id_map.get(raw_id, raw_id),
                "title": task.get("title") or task.get("name") or f"task {index + 1}",
                "description": task.get("description") or task.get("details") or task.get("summary") or task.get("title") or task.get("name"),
                "task_type": _TYPE_ALIASES.get(task_type, task_type),
                "priority": _PRIORITY_ALIASES.get(priority, priority),
                "dependencies": [_alias_id(item, id_map) for item in _as_list(task.get("dependencies"))],
                "related_files": _as_list(task.get("related_files") or task.get("files")),
                "acceptance_criteria": _as_list(task.get("acceptance_criteria") or task.get("acceptance")),
                "estimated_complexity": _COMPLEXITY_ALIASES.get(complexity, complexity),
                "source_findings": _as_list(task.get("source_findings") or task.get("findings")),
                "source_acceptance_criteria": _as_list(task.get("source_acceptance_criteria") or task.get("criteria")),
                "assumptions": _as_list(task.get("assumptions")),
                "blocking_clarification_ids": _as_list(task.get("blocking_clarification_ids") or task.get("blocked_by")),
            }
        )
    clarifications = []
    for index, item in enumerate(data.get("clarification_items") or data.get("clarifications") or []):
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or item.get("title") or "").strip()
        if not question:
            continue
        raw_id = str(item.get("clarification_id") or item.get("id") or f"c{index + 1}")
        clarifications.append(
            {
                "clarification_id": raw_id if len(raw_id) <= 40 else _stable(raw_id),
                "question": question[:500],
                "context": str(item.get("context") or question)[:1000],
                "impact": str(item.get("impact") or "该问题尚未确认，不能写成已确认需求。")[:500],
                "blocking": True,
                "affected_task_ids": [_alias_id(task_id, id_map) for task_id in _as_list(item.get("affected_task_ids"))][:12],
                "status": "unresolved",
                "source": str(item.get("source") or "research_report")[:64],
            }
        )
    risks = []
    for index, item in enumerate(data.get("risks") or []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        description = str(item.get("description") or title).strip()
        if not title or not description:
            continue
        raw_id = str(item.get("risk_id") or item.get("id") or f"r{index + 1}")
        risks.append(
            {
                "risk_id": raw_id if len(raw_id) <= 40 else _stable(raw_id),
                "title": title[:200],
                "description": description[:1000],
                "severity": str(item.get("severity") or "medium").lower(),
                "likelihood": str(item.get("likelihood") or "medium").lower(),
                "mitigation": str(item.get("mitigation") or "保持相关实现任务阻塞，直到问题被回答。")[:1000],
                "affected_task_ids": [_alias_id(task_id, id_map) for task_id in _as_list(item.get("affected_task_ids"))][:12],
                "source": str(item.get("source") or "research_report")[:64],
            }
        )
    return {
        "planning_summary": str(data.get("planning_summary") or data.get("summary") or "根据研究报告拆分任务，未确认问题保持阻塞。")[:2000],
        "tasks": rewritten_tasks,
        "clarification_items": clarifications[:12],
        "risks": risks[:12],
        "assumptions": _as_list(data.get("assumptions"))[:12],
        "limitations": _as_list(data.get("limitations"))[:12],
    }


def _alias_id(value, id_map: dict[str, str]) -> str:
    text = str(value)
    if text in id_map:
        return id_map[text]
    return text if len(text) <= 40 else _stable(text)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


_COMPLEXITY_ALIASES = {
    "low": "small",
    "trivial": "small",
    "easy": "small",
    "small": "small",
    "medium": "medium",
    "moderate": "medium",
    "normal": "medium",
    "large": "large",
    "high": "large",
    "complex": "large",
    "unknown": "unknown",
}
_PRIORITY_ALIASES = {
    "critical": "critical",
    "urgent": "critical",
    "blocker": "critical",
    "high": "high",
    "medium": "medium",
    "normal": "medium",
    "low": "low",
}
_TYPE_ALIASES = {
    "implementation": "implementation",
    "coding": "implementation",
    "code": "implementation",
    "feature": "implementation",
    "testing": "testing",
    "test": "testing",
    "refactoring": "refactoring",
    "refactor": "refactoring",
    "documentation": "documentation",
    "docs": "documentation",
    "doc": "documentation",
    "investigation": "investigation",
    "research": "investigation",
    "spike": "investigation",
}


def _parse(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    start = raw.find("{")
    if start > 0:
        raw = raw[start:]
    candidates = [raw]
    end = raw.rfind("}")
    if end > 0:
        candidates.append(raw[: end + 1])
    candidates.append(_close_json(raw))
    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        if isinstance(data, dict):
            return data
    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("object required", raw, 0)


def _close_json(raw: str) -> str:
    out: list[str] = []
    stack: list[str] = []
    in_string = False
    escape = False
    for char in raw:
        out.append(char)
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append("}")
        elif char == "[":
            stack.append("]")
        elif char in "}]" and stack and stack[-1] == char:
            stack.pop()
    if in_string:
        out.append('"')
    text = "".join(out).rstrip()
    while text.endswith(","):
        text = text[:-1].rstrip()
    return text + "".join(reversed(stack))


def _stable(seed: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


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
    usage = {
        "provider": log.get("provider") or settings.llm_provider,
        "model": log.get("model") or settings.llm_model,
        "prompt_tokens": prompt if isinstance(prompt, int) else None,
        "completion_tokens": completion if isinstance(completion, int) else None,
        "latency_seconds": log.get("elapsed_seconds") if isinstance(log.get("elapsed_seconds"), (int, float)) else None,
    }
    cost = raw.get("estimated_cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        usage["estimated_cost"] = float(cost)
    return usage


def _add_usage(left: dict | None, right: dict | None) -> dict | None:
    if left is None:
        return right
    if right is None:
        return left
    merged = dict(left)
    for key in ("prompt_tokens", "completion_tokens"):
        if isinstance(left.get(key), int) and isinstance(right.get(key), int):
            merged[key] = left[key] + right[key]
    if isinstance(left.get("latency_seconds"), (int, float)) and isinstance(right.get("latency_seconds"), (int, float)):
        merged["latency_seconds"] = float(left["latency_seconds"]) + float(right["latency_seconds"])
    return merged
