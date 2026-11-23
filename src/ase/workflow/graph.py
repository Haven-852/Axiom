import uuid
from collections import defaultdict, deque
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ase.agents.lightweight import analyze_requirement, evaluate, plan_tasks
from ase.agents.llm_client import diagnose_failure
from ase.contracts.domain import (
    AgentTaskRequest,
    Approval,
    ApprovalStatus,
    Artifact,
    EvaluationReport,
    EventSource,
    Execution,
    ExecutionEvent,
    ExecutionStatus,
    ModelUsage,
    OperationRecord,
    RequirementAnalysis,
    TaskStatus,
    TestResult,
    WorkflowStatus,
    new_id,
)
from ase.db.session import session_scope
from ase.infra.fault import crash_point
from ase.infra.git_workspace import GitWorkspaceManager
from ase.store import (
    add_checkpoint_index,
    get_project,
    save_analysis,
    save_approval,
    save_artifact,
    save_evaluation,
    claim_operation,
    get_execution,
    get_operation,
    save_event,
    save_execution,
    save_model_usage,
    save_operation,
    save_plan,
    save_test_result,
    set_approval,
    update_execution,
    update_workflow,
)
from ase.workflow.runtime import current_context


class GraphState(TypedDict, total=False):
    project_id: str
    requirement_id: str
    workflow_id: str
    statement: str
    analysis: dict[str, Any]
    plan: dict[str, Any]
    approval_id: str
    approval_decision: str
    workspace: dict[str, Any]
    execution_id: str
    test_result_id: str
    test_passed: bool
    repair_count: int
    max_repairs: int
    diagnosis: str
    diff: str
    evaluation: dict[str, Any]
    container_id: str
    status: str
    error: str


def build_graph(checkpointer: Any):
    graph = StateGraph(GraphState)
    graph.add_node("analyze", _analyze)
    graph.add_node("plan", _plan)
    graph.add_node("validate", _validate)
    graph.add_node("request_approval", _request_approval)
    graph.add_node("human_approval", _human_approval)
    graph.add_node("workspace", _workspace)
    graph.add_node("develop", _develop)
    graph.add_node("test", _test)
    graph.add_node("diagnose", _diagnose)
    graph.add_node("repair", _repair)
    graph.add_node("evaluate", _evaluate)
    graph.add_node("deliver", _deliver)
    graph.add_edge(START, "analyze")
    graph.add_edge("analyze", "plan")
    graph.add_edge("plan", "validate")
    graph.add_conditional_edges("validate", _route_plan, {"approval": "request_approval", "end": END})
    graph.add_edge("request_approval", "human_approval")
    graph.add_conditional_edges("human_approval", _route_approval, {"workspace": "workspace", "end": END})
    graph.add_edge("workspace", "develop")
    graph.add_edge("develop", "test")
    graph.add_conditional_edges(
        "test",
        _route_test,
        {"evaluate": "evaluate", "diagnose": "diagnose"},
    )
    graph.add_edge("diagnose", "repair")
    graph.add_edge("repair", "test")
    graph.add_edge("evaluate", "deliver")
    graph.add_edge("deliver", END)
    return graph.compile(checkpointer=checkpointer)


def _analyze(state: GraphState) -> dict[str, Any]:
    analysis = analyze_requirement(state["requirement_id"], state["statement"])
    _record(state, "Requirement Analyzed", EventSource.ASE, {"analysis_id": analysis.id}, analysis=analysis)
    _status(state, WorkflowStatus.RUNNING, "analyze")
    return {"analysis": analysis.model_dump(mode="json"), "status": WorkflowStatus.RUNNING.value}


def _plan(state: GraphState) -> dict[str, Any]:
    analysis = RequirementAnalysis.model_validate(state["analysis"])
    plan = plan_tasks(state["requirement_id"], analysis)
    _record(state, "Plan Generated", EventSource.ASE, {"plan_id": plan.id}, plan=plan)
    return {"plan": plan.model_dump(mode="json")}


def _validate(state: GraphState) -> dict[str, Any]:
    plan = state["plan"]
    error = _cycle_error(plan)
    if error:
        _status(state, WorkflowStatus.FAILED, "validate", error)
        return {"status": WorkflowStatus.FAILED.value, "error": error}
    return {"status": WorkflowStatus.RUNNING.value, "error": ""}


def _request_approval(state: GraphState) -> dict[str, Any]:
    approval = Approval(
        project_id=state["project_id"],
        workflow_id=state["workflow_id"],
        subject=state["plan"]["summary"],
        status=ApprovalStatus.PENDING,
    )
    ctx = current_context()
    with session_scope(ctx.sessions) as session:
        save_approval(session, approval)
        update_workflow(session, state["workflow_id"], WorkflowStatus.WAITING_APPROVAL.value)
        add_checkpoint_index(session, state["workflow_id"], "request_approval")
        save_event(
            session,
            ExecutionEvent(
                project_id=state["project_id"],
                workflow_id=state["workflow_id"],
                event_type="Approval Requested",
                source=EventSource.ASE,
                payload={"approval_id": approval.id},
            ),
        )
    return {"approval_id": approval.id, "status": WorkflowStatus.WAITING_APPROVAL.value}


def _human_approval(state: GraphState) -> dict[str, Any]:
    decision = interrupt({"approval_id": state["approval_id"], "workflow_id": state["workflow_id"]})
    if isinstance(decision, dict):
        value = str(decision.get("decision", "rejected"))
        comment = str(decision.get("comment", ""))
    else:
        value = str(decision)
        comment = ""
    status = ApprovalStatus.APPROVED if value == "approved" else ApprovalStatus.REJECTED
    ctx = current_context()
    with session_scope(ctx.sessions) as session:
        set_approval(session, state["approval_id"], status.value, comment)
        save_event(
            session,
            ExecutionEvent(
                project_id=state["project_id"],
                workflow_id=state["workflow_id"],
                event_type="Approval Completed",
                source=EventSource.LANGGRAPH,
                payload={"approval_id": state["approval_id"], "decision": value},
            ),
        )
    workflow_status = WorkflowStatus.DEVELOPING if value == "approved" else WorkflowStatus.CANCELLED
    _status(state, workflow_status, "human_approval")
    return {"approval_decision": value, "status": workflow_status.value}


def _workspace(state: GraphState) -> dict[str, Any]:
    ctx = current_context()
    with session_scope(ctx.sessions) as session:
        project = get_project(session, state["project_id"])
    if project is None:
        raise RuntimeError("project missing")
    task_id = state["plan"]["tasks"][0]["id"]
    info = ctx.git.create_workspace(project.id, task_id, project.repo_path, project.base_ref)
    _record(state, "Workspace Created", EventSource.ASE, info.model_dump())
    _status(state, WorkflowStatus.DEVELOPING, "workspace")
    return {"workspace": info.model_dump(), "status": WorkflowStatus.DEVELOPING.value}


def _develop(state: GraphState) -> dict[str, Any]:
    return _run_coding(state, repair=False)


def _repair(state: GraphState) -> dict[str, Any]:
    count = int(state.get("repair_count", 0)) + 1
    result = _run_coding({**state, "repair_count": count}, repair=True)
    result["repair_count"] = count
    _record(state, "Repair Completed", EventSource.ASE, {"repair_count": count})
    return result


def _load_or_create_execution(state, task_id: str, operation_id: str, attempt: int, conversation_id: str, container_id: str) -> Execution:
    ctx = current_context()
    with session_scope(ctx.sessions) as session:
        op = get_operation(session, operation_id)
        current = None if op is None or not op.execution_id else get_execution(session, op.execution_id)
        if current is None:
            current = Execution(
                project_id=state["project_id"],
                workflow_id=state["workflow_id"],
                task_id=task_id,
                agent_id="coding",
                status=ExecutionStatus.CREATED,
                workspace_id=state["workspace"]["workspace_id"],
                conversation_id=conversation_id or None,
                container_id=container_id or None,
                operation_id=operation_id,
                attempt=attempt,
            )
            save_execution(session, current)
        current.conversation_id = conversation_id or current.conversation_id
        current.container_id = container_id or current.container_id
        if current.status == ExecutionStatus.CREATED:
            current.status = ExecutionStatus.STARTING
            update_execution(session, current)
        if current.status == ExecutionStatus.STARTING:
            current.status = ExecutionStatus.RUNNING
            update_execution(session, current)
        else:
            update_execution(session, current)
        if op is not None:
            op.execution_id = current.id
            op.conversation_id = current.conversation_id
            op.container_id = current.container_id
            save_operation(session, op)
        return current


def _model_usage(settings, state, task_id: str, execution_id: str, usage: dict | None) -> ModelUsage | None:
    if not usage:
        return None
    metrics = (usage.get("usage_to_metrics") or {}).get("default") or {}
    tokens = metrics.get("accumulated_token_usage") or {}
    prompt = tokens.get("prompt_tokens")
    completion = tokens.get("completion_tokens")
    if not isinstance(prompt, int) and not isinstance(completion, int):
        return None
    total = prompt + completion if isinstance(prompt, int) and isinstance(completion, int) else None
    cost = metrics.get("accumulated_cost")
    return ModelUsage(
        project_id=state["project_id"],
        workflow_id=state["workflow_id"],
        task_id=task_id,
        execution_id=execution_id,
        provider=settings.llm_provider,
        model=str(metrics.get("model_name") or settings.llm_model),
        prompt_tokens=prompt if isinstance(prompt, int) else None,
        completion_tokens=completion if isinstance(completion, int) else None,
        total_tokens=total,
        estimated_cost=float(cost) if isinstance(cost, (int, float)) else None,
    )


def _run_coding(state: GraphState, repair: bool) -> dict[str, Any]:
    ctx = current_context()
    task = state["plan"]["tasks"][0]
    instruction = task["instruction"]
    if repair:
        instruction = instruction + "\n\n诊断结果：\n" + state.get("diagnosis", "")
    attempt = int(state.get("repair_count", 0)) + 1
    kind = "repair" if repair else "develop"
    operation = OperationRecord(
        id=f"{state['workflow_id']}:{task['id']}:{kind}:{attempt}",
        project_id=state["project_id"],
        workflow_id=state["workflow_id"],
        task_id=task["id"],
        kind=kind,
        attempt=attempt,
        status="running",
    )
    with session_scope(ctx.sessions) as session:
        operation = claim_operation(session, operation)
    if operation.status == "completed" and operation.execution_id:
        return {
            "execution_id": operation.execution_id,
            "container_id": operation.container_id or state.get("container_id", ""),
            "error": "",
        }
    conversation_id = operation.conversation_id or ""
    if ctx.settings.coding_runtime == "openhands" and not conversation_id:
        conversation_id = str(uuid.uuid4())
    container_id = operation.container_id or state.get("container_id", "")
    agent_host = ""
    if ctx.settings.coding_runtime == "openhands":
        handle = ctx.containers.start(__import__("pathlib").Path(state["workspace"]["path"]))
        container_id = handle.container_id
        agent_host = handle.host
        _record(
            state,
            "Workspace Created",
            EventSource.DOCKER,
            {"container_id": container_id, "image": handle.image, "limits": handle.limits},
        )
    execution = _load_or_create_execution(state, task["id"], operation.id, attempt, conversation_id, container_id)
    operation.execution_id = execution.id
    operation.conversation_id = conversation_id or None
    operation.container_id = container_id or None
    operation.status = "running"
    with session_scope(ctx.sessions) as session:
        save_operation(session, operation)
        update_workflow(session, state["workflow_id"], WorkflowStatus.REPAIRING.value if repair else WorkflowStatus.DEVELOPING.value)
    import asyncio

    session_info = asyncio.run(ctx.coding.create_session(state["workspace"]["path"]))
    request = AgentTaskRequest(
        task_id=task["id"],
        instruction=instruction,
        workspace_path=state["workspace"]["path"],
        timeout_seconds=ctx.settings.agent_timeout_seconds,
        agent_host=agent_host,
        conversation_id=conversation_id,
    )
    outcome = asyncio.run(ctx.coding.execute_task(request, session_info))
    crash_point("before_execution_commit")
    execution.status = outcome.status
    execution.session_id = outcome.session_id
    execution.conversation_id = outcome.conversation_id or conversation_id or execution.conversation_id
    execution.container_id = container_id or execution.container_id
    execution.error = outcome.error
    operation.status = "completed" if outcome.status == ExecutionStatus.SUCCEEDED else "failed"
    operation.conversation_id = execution.conversation_id
    operation.container_id = execution.container_id
    operation.execution_id = execution.id
    usage = _model_usage(ctx.settings, state, task["id"], execution.id, outcome.usage)
    source = EventSource.OPENHANDS if session_info.runtime == "openhands" else EventSource.ASE
    with session_scope(ctx.sessions) as session:
        update_execution(session, execution)
        save_operation(session, operation)
        if usage is not None:
            save_model_usage(session, usage)
        save_event(
            session,
            ExecutionEvent(
                project_id=state["project_id"],
                workflow_id=state["workflow_id"],
                task_id=task["id"],
                execution_id=execution.id,
                event_type="Agent Started",
                source=source,
                payload={
                    "session_id": outcome.session_id,
                    "runtime": session_info.runtime,
                    "conversation_id": execution.conversation_id,
                },
            ),
        )
        for event in outcome.events:
            save_event(
                session,
                ExecutionEvent(
                    project_id=state["project_id"],
                    workflow_id=state["workflow_id"],
                    task_id=task["id"],
                    execution_id=execution.id,
                    event_type=(
                    "Tool Execution"
                    if event.get("tool") or "Action" in str(event.get("type"))
                    else "Agent Message"
                ),
                    source=source,
                    payload=event,
                ),
            )
        if outcome.error:
            save_event(
                session,
                ExecutionEvent(
                    project_id=state["project_id"],
                    workflow_id=state["workflow_id"],
                    task_id=task["id"],
                    execution_id=execution.id,
                    event_type="Agent Message",
                    source=source,
                    payload={"error": outcome.error},
                ),
            )
    asyncio.run(ctx.coding.close_session(session_info.session_id))
    if outcome.status not in {ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED}:
        return {"execution_id": execution.id, "container_id": container_id, "error": outcome.error or outcome.status.value}
    return {"execution_id": execution.id, "container_id": container_id, "error": outcome.error or ""}


def _test(state: GraphState) -> dict[str, Any]:
    ctx = current_context()
    execution_id = state.get("execution_id") or new_id()
    if state.get("execution_id"):
        from sqlalchemy import select

        from ase.db.orm import TestResultRow

        with session_scope(ctx.sessions) as session:
            existing_id = session.scalar(
                select(TestResultRow.id)
                .where(TestResultRow.execution_id == execution_id, TestResultRow.passed.is_(True))
                .limit(1)
            )
        if existing_id:
            return {
                "test_result_id": existing_id,
                "test_passed": True,
                "status": WorkflowStatus.TESTING.value,
            }
    _record(state, "Test Started", EventSource.DOCKER, {"execution_id": execution_id})
    result = ctx.sandbox.run_pytest(
        __import__("pathlib").Path(state["workspace"]["path"]),
        execution_id,
        state.get("container_id"),
    )
    _status(state, WorkflowStatus.TESTING, "test")
    ctx_events_output = result.output
    with session_scope(ctx.sessions) as session:
        save_test_result(session, result)
        save_event(
            session,
            ExecutionEvent(
                project_id=state["project_id"],
                workflow_id=state["workflow_id"],
                execution_id=execution_id,
                event_type="Test Completed",
                source=EventSource.DOCKER,
                payload={
                    "passed": result.passed,
                    "exit_code": result.exit_code,
                    "command": result.command,
                    "duration_seconds": result.duration_seconds,
                    "passed_count": result.passed_count,
                    "failed_count": result.failed_count,
                    "output": ctx_events_output[-2000:],
                },
            ),
        )
    return {"test_result_id": result.id, "test_passed": result.passed, "status": WorkflowStatus.TESTING.value}


def _diagnose(state: GraphState) -> dict[str, Any]:
    ctx = current_context()
    with session_scope(ctx.sessions) as session:
        from ase.db.orm import TestResultRow

        row = session.get(TestResultRow, state["test_result_id"])
        output = row.output if row is not None else ""
    diagnosis = diagnose_failure(ctx.settings, output)
    note = diagnosis.cause + "\n" + diagnosis.repair_plan
    _record(
        state,
        "Diagnosis Completed",
        EventSource.ASE,
        diagnosis.model_dump(),
    )
    _status(state, WorkflowStatus.DIAGNOSING, "diagnose")
    return {"diagnosis": note, "status": WorkflowStatus.DIAGNOSING.value}


def _evaluate(state: GraphState) -> dict[str, Any]:
    ctx = current_context()
    if state.get("test_passed"):
        crash_point("before_final_commit")
    from ase.contracts.domain import WorkspaceInfo

    info = WorkspaceInfo.model_validate(state["workspace"])
    diff = ctx.git.get_diff(info)
    report = evaluate(state["workflow_id"], bool(state.get("test_passed")), diff)
    with session_scope(ctx.sessions) as session:
        save_evaluation(session, report)
        update_workflow(session, state["workflow_id"], WorkflowStatus.EVALUATING.value)
        save_event(
            session,
            ExecutionEvent(
                project_id=state["project_id"],
                workflow_id=state["workflow_id"],
                event_type="Evaluation Completed",
                source=EventSource.ASE,
                payload={
                    **report.model_dump(mode="json"),
                    "observed": {
                        "repair_count": int(state.get("repair_count", 0)),
                        "diff_characters": len(diff),
                        "test_passed": bool(state.get("test_passed")),
                    },
                },
            ),
        )
    return {"diff": diff, "evaluation": report.model_dump(mode="json"), "status": WorkflowStatus.EVALUATING.value}


def _deliver(state: GraphState) -> dict[str, Any]:
    ctx = current_context()
    from ase.contracts.domain import WorkspaceInfo

    info = WorkspaceInfo.model_validate(state["workspace"])
    report = EvaluationReport.model_validate(state["evaluation"])
    commit = ""
    if state.get("test_passed"):
        commit = ctx.git.commit_changes(info, "ASE task branch delivery")
    artifact = Artifact(
        project_id=state["project_id"],
        workflow_id=state["workflow_id"],
        execution_id=state.get("execution_id"),
        kind="git_diff",
        uri=f"workspace://{info.workspace_id}",
        body=state.get("diff", ""),
    )
    if state.get("container_id"):
        ctx.containers.stop(state["container_id"])
    status = WorkflowStatus.DELIVERED if report.passed else WorkflowStatus.FAILED
    with session_scope(ctx.sessions) as session:
        save_artifact(session, artifact)
        update_workflow(session, state["workflow_id"], status.value, None if report.passed else report.summary)
        save_event(
            session,
            ExecutionEvent(
                project_id=state["project_id"],
                workflow_id=state["workflow_id"],
                event_type="Delivery Completed",
                source=EventSource.ASE,
                payload={"artifact_id": artifact.id, "commit": commit, "passed": report.passed},
            ),
        )
    return {"status": status.value}


def _route_plan(state: GraphState) -> str:
    return "end" if state.get("error") else "approval"


def _route_approval(state: GraphState) -> str:
    return "workspace" if state.get("approval_decision") == "approved" else "end"


def _route_test(state: GraphState) -> str:
    if state.get("test_passed"):
        return "evaluate"
    if int(state.get("repair_count", 0)) >= int(state.get("max_repairs", 2)):
        return "evaluate"
    return "diagnose"


def _cycle_error(plan: dict[str, Any]) -> str:
    tasks = {item["id"] for item in plan["tasks"]}
    incoming = {task_id: 0 for task_id in tasks}
    graph: dict[str, list[str]] = defaultdict(list)
    for edge in plan["dependencies"]:
        if edge["predecessor_id"] not in tasks or edge["successor_id"] not in tasks:
            return "dependency points at an unknown task"
        graph[edge["predecessor_id"]].append(edge["successor_id"])
        incoming[edge["successor_id"]] += 1
    queue = deque(task_id for task_id, count in incoming.items() if count == 0)
    seen = 0
    while queue:
        node = queue.popleft()
        seen += 1
        for nxt in graph[node]:
            incoming[nxt] -= 1
            if incoming[nxt] == 0:
                queue.append(nxt)
    if seen != len(tasks):
        return "task dependencies contain a cycle"
    return ""


def _record(state: GraphState, event_type: str, source: EventSource, payload: dict[str, Any], analysis=None, plan=None) -> None:
    ctx = current_context()
    with session_scope(ctx.sessions) as session:
        if analysis is not None:
            save_analysis(session, analysis)
        if plan is not None:
            save_plan(session, plan)
        save_event(
            session,
            ExecutionEvent(
                project_id=state["project_id"],
                workflow_id=state["workflow_id"],
                event_type=event_type,
                source=source,
                payload=payload,
            ),
        )


def _status(state: GraphState, status: WorkflowStatus, node: str, error: str | None = None) -> None:
    ctx = current_context()
    with session_scope(ctx.sessions) as session:
        update_workflow(session, state["workflow_id"], status.value, error)
        add_checkpoint_index(session, state["workflow_id"], node)


_ = TaskStatus
_ = GitWorkspaceManager
_ = TestResult
