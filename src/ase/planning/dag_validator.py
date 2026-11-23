"""Deterministic DAG checks. Model output is not trusted."""

from ase.agents.research.schemas import ResearchReport
from ase.planning.schemas import Plan, PlanValidationResult

_CODE_TYPES = {"implementation", "testing", "refactoring"}
_DECIDED = ("已确认完整表达式", "已确认只支持字面量", "用户要求完整表达式", "用户要求仅支持字面量", "用户已确认")


def finalize_plan(
    plan: Plan,
    report: ResearchReport,
    resolutions: dict[str, str] | None = None,
    deferred: set[str] | None = None,
) -> tuple[Plan, PlanValidationResult]:
    """Fill execution order from the checked graph, then validate that sealed plan."""

    errors, _warnings, blocked, full_order = _structure(plan, report, resolutions, deferred)
    if errors or not full_order:
        result = _result(False, errors or ["task dependencies contain a cycle"], [], len(plan.tasks), len(blocked), 0, [])
        sealed = plan.model_copy(update={"execution_order": [], "validation_result": result})
        return sealed, result
    executable = [task_id for task_id in full_order if task_id not in blocked]
    sealed = plan.model_copy(update={"execution_order": executable})
    result = validate_plan(sealed, report, resolutions, deferred)
    return sealed.model_copy(update={"validation_result": result}), result


def validate_plan(
    plan: Plan,
    report: ResearchReport,
    resolutions: dict[str, str] | None = None,
    deferred: set[str] | None = None,
) -> PlanValidationResult:
    errors, warnings, blocked, full_order = _structure(plan, report, resolutions, deferred)
    if errors or not full_order:
        return _result(False, errors or ["task dependencies contain a cycle"], warnings, len(plan.tasks), len(blocked), 0, [])
    executable = [task_id for task_id in full_order if task_id not in blocked]
    if plan.execution_order != executable:
        errors.append("execution_order does not match the executable topological order")
    for index, task_id in enumerate(plan.execution_order):
        if task_id in blocked:
            errors.append(f"execution_order includes blocked task {task_id}")
            continue
        task = next((item for item in plan.tasks if item.task_id == task_id), None)
        if task is None:
            errors.append(f"execution_order includes missing task {task_id}")
            continue
        for dependency in task.dependencies:
            if dependency in executable and plan.execution_order.index(dependency) > index:
                errors.append(f"execution_order runs {task_id} before dependency {dependency}")
    return _result(not errors, errors, warnings, len(plan.tasks), len(blocked), len(executable), full_order)


def blocked_task_ids(plan: Plan) -> list[str]:
    return [task.task_id for task in plan.tasks if task.task_id not in set(plan.execution_order)]


def _structure(
    plan: Plan,
    report: ResearchReport,
    resolutions: dict[str, str] | None = None,
    deferred: set[str] | None = None,
) -> tuple[list[str], list[str], set[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    ids = [task.task_id for task in plan.tasks]
    if len(ids) != len(set(ids)):
        errors.append("task_id is duplicated")
    known = set(ids)
    finding_ids = {item.finding_id for item in report.code_findings}
    criterion_ids = {item.criterion_id for item in report.acceptance_criteria}
    known_files = {item.file_path for item in report.code_findings}
    inferred = {item.criterion_id for item in report.acceptance_criteria if item.source.value == "inferred"}
    answered = {question: text.strip() for question, text in (resolutions or {}).items() if text and text.strip()}
    unresolved_questions = [question for question in report.clarification_questions if question not in answered]
    scope_open = bool(unresolved_questions) or any(item.blocking and item.status.value == "unresolved" for item in plan.clarification_items)
    for item in plan.clarification_items:
        if item.status.value == "answered":
            if not answered or item.question not in answered:
                errors.append(f"clarification {item.clarification_id} was answered without a user decision")
            elif item.answer and item.answer != answered[item.question]:
                errors.append(f"clarification {item.clarification_id} does not match the stored user decision")
        elif item.status.value == "deferred":
            if not deferred or item.question not in deferred:
                errors.append(f"clarification {item.clarification_id} was deferred without a user decision")
            elif item.blocking:
                scope_open = True
        elif item.status.value != "unresolved":
            errors.append(f"clarification {item.clarification_id} is not unresolved")
    blocked: set[str] = set()
    for task in plan.tasks:
        text = f"{task.title}\n{task.description}"
        if _claims_unconfirmed_scope(text, answered):
            errors.append(f"task {task.task_id} states an unconfirmed scope as a requirement")
        if task.task_id in task.dependencies:
            errors.append(f"task {task.task_id} depends on itself")
        for dependency in task.dependencies:
            if dependency not in known:
                errors.append(f"task {task.task_id} depends on missing task {dependency}")
        if not task.acceptance_criteria:
            errors.append(f"task {task.task_id} has no acceptance criteria")
        if not task.source_findings and not task.source_acceptance_criteria:
            errors.append(f"task {task.task_id} has no research evidence")
        for finding_id in task.source_findings:
            if finding_id not in finding_ids:
                errors.append(f"task {task.task_id} cites missing finding {finding_id}")
        for criterion_id in task.source_acceptance_criteria:
            if criterion_id not in criterion_ids:
                errors.append(f"task {task.task_id} cites missing criterion {criterion_id}")
            elif criterion_id in inferred and not any("推断" in note for note in task.assumptions):
                errors.append(f"task {task.task_id} uses inferred criterion {criterion_id} without marking it")
        for path in task.related_files:
            if path not in known_files:
                errors.append(f"task {task.task_id} cites a file that is not in the research report: {path}")
        if task.blocking_clarification_ids or (scope_open and task.task_type.value in _CODE_TYPES):
            blocked.add(task.task_id)
    if errors:
        return errors, warnings, blocked, []
    changed = True
    while changed:
        changed = False
        for task in plan.tasks:
            if task.task_id in blocked:
                continue
            if any(dependency in blocked for dependency in task.dependencies):
                blocked.add(task.task_id)
                changed = True
    order, cycle = _topo(plan.tasks)
    if cycle:
        return ["task dependencies contain a cycle"], warnings, blocked, []
    for task in plan.tasks:
        if any(criterion_id in inferred for criterion_id in task.source_acceptance_criteria):
            warnings.append(f"task {task.task_id} uses an inferred acceptance criterion")
    if report.limitations:
        warnings.append("research limitations remain in force")
    return [], warnings, blocked, order


def _claims_unconfirmed_scope(text: str, answered: dict[str, str]) -> bool:
    if not answered:
        return any(phrase in text for phrase in _DECIDED)
    if any(phrase in text for phrase in ("已确认只支持字面量", "用户要求仅支持字面量", "只支持字面量")):
        return any("完整" in value for value in answered.values())
    return False


def _topo(tasks) -> tuple[list[str], bool]:
    incoming = {task.task_id: 0 for task in tasks}
    children: dict[str, list[str]] = {task.task_id: [] for task in tasks}
    for task in tasks:
        for dependency in task.dependencies:
            if dependency not in incoming:
                continue
            children[dependency].append(task.task_id)
            incoming[task.task_id] += 1
    ready = sorted(task_id for task_id, count in incoming.items() if count == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for child in children[current]:
            incoming[child] -= 1
            if incoming[child] == 0:
                ready.append(child)
                ready.sort()
    return order, len(order) != len(tasks)


def _result(valid: bool, errors: list[str], warnings: list[str], count: int, blocked: int, executable: int, order: list[str]) -> PlanValidationResult:
    return PlanValidationResult(
        valid=valid,
        structurally_valid=valid,
        errors=errors[:30],
        warnings=warnings[:30],
        task_count=count,
        blocked_task_count=blocked,
        executable_task_count=executable,
        topological_order=order,
    )
