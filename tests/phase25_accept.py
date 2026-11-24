"""Phase 2.5 real DeepSeek acceptance. Not collected by pytest."""

import json
import subprocess
import sys
import time
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import create_engine, text

from ase.agents.coding_agent import CodingAgent, coding_definition
from ase.agents.registry import AgentRegistry
from ase.agents.runner import AgentRunner
from ase.app import build_context
from ase.config import Settings
from ase.contracts.domain import Project, new_id
from ase.db.session import session_scope
from ase.execution.coding_service import CodingTaskService
from ase.orchestration.research_service import ResearchWorkflowService
from ase.planning.review_service import PlanReviewService, plan_hash
from ase.planning.schemas import Plan
from ase.planning.service import PlanningWorkflowService
from ase.store import get_artifact, get_workflow, list_artifacts, save_project, save_test_result

URL = "postgresql+psycopg://ase:ase@127.0.0.1:5433/ase"
REPO = Path(r"E:\demo\ase-phase25-coding-validation")
WORKSPACES = Path(r"E:\demo\ase-phase25-workspaces")
STATE_PATH = Path(r"E:\demo\Axiom\var\phase25\state.json")
CALCULATOR = Path(r"E:\demo\Axiom\ase-poc-demo\calculator")
CALCULATOR_WORKFLOW = "1dd7c84d-36d1-4dcb-a66e-1004e0a1985f"
REQUIREMENT = """实现已有函数 normalize_text。仓库、函数签名和 pytest 都已经存在，没有待确认项。
计划只包含一个实现任务和一个测试任务。实现任务没有前置依赖，只修改 src/string_utils.py。测试任务依赖实现任务，只运行现有 pytest。
已经确认的行为：
1. 输入是字符串。
2. 去掉首尾空白。
3. 连续空白字符压缩成一个空格。空白包括空格、制表符和换行。
4. 转换为小写。
5. 保持 def normalize_text(value: str) -> str。
6. 不引入第三方依赖。
7. 不修改 tests/test_string_utils.py。
8. normalize_text("  Hello   WORLD  ") == "hello world"。
9. 空字符串或只含空白时返回空字符串。
10. 测试文件里的制表符和换行用例已经写好，实现要让这些真实空白字符通过。
"""
ANSWER = (
    "实现 normalize_text：去掉首尾空白，把连续空格、制表符和换行压成一个空格，再转小写。"
    "空字符串或全空白返回空字符串。保持 def normalize_text(value: str) -> str。"
    "不引入第三方依赖。不修改 tests/test_string_utils.py。"
    "normalize_text('  Hello   WORLD  ') == 'hello world'，normalize_text('A\\t\\tB\\n\\nC') == 'a b c'。"
)


def settings() -> Settings:
    base = Settings()
    if not base.llm_api_key.get_secret_value():
        raise SystemExit("model key is missing")
    return base.model_copy(
        update={
            "database_url": URL,
            "workspace_root": WORKSPACES,
            "coding_runtime": "openhands",
            "checkpoint_backend": "memory",
        }
    )


def context():
    ctx = build_context(settings())
    registry = AgentRegistry()
    registry.register(coding_definition(), lambda: CodingAgent(ctx.coding, ctx.sessions))
    service = CodingTaskService(ctx, AgentRunner(registry, ctx.sessions, ctx.settings))
    review = PlanReviewService(PlanningWorkflowService(ctx, MemorySaver()))
    research = ResearchWorkflowService(ctx, MemorySaver())
    return ctx, service, review, research


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def ensure_repo() -> None:
    if (REPO / ".git").exists():
        return
    (REPO / "src").mkdir(parents=True)
    (REPO / "tests").mkdir()
    (REPO / "src" / "string_utils.py").write_text(
        "def normalize_text(value: str) -> str:\n    raise NotImplementedError\n",
        encoding="utf-8",
    )
    (REPO / "tests" / "test_string_utils.py").write_text(
        "from string_utils import normalize_text\n\n\n"
        "def test_normalize_text():\n"
        '    assert normalize_text("  Hello   WORLD  ") == "hello world"\n\n\n'
        "def test_blank():\n"
        '    assert normalize_text("   ") == ""\n\n\n'
        "def test_tabs_and_newlines():\n"
        '    assert normalize_text("A\\t\\tB\\n\\nC") == "a b c"\n',
        encoding="utf-8",
    )
    (REPO / "README.md").write_text(
        "# ase-phase25-coding-validation\n\nDisposable coding fixture. It is not the calculator project.\n",
        encoding="utf-8",
    )
    (REPO / "pytest.ini").write_text("[pytest]\npythonpath = src\n", encoding="utf-8")
    git = ["git", "-c", "core.autocrlf=false"]
    subprocess.run([*git, "init", "-b", "main"], cwd=REPO, check=True)
    subprocess.run([*git, "add", "."], cwd=REPO, check=True)
    subprocess.run(
        [*git, "-c", "user.email=ase@example.com", "-c", "user.name=ASE", "commit", "-m", "initial string utility"],
        cwd=REPO,
        check=True,
    )


def engine():
    return create_engine(URL, pool_pre_ping=True)


def calculator_guard() -> dict:
    with engine().connect() as connection:
        count = connection.execute(
            text("select count(*) from executions where workflow_id = :id"),
            {"id": CALCULATOR_WORKFLOW},
        ).scalar()
        status = connection.execute(
            text("select status from workflows where id = :id"),
            {"id": CALCULATOR_WORKFLOW},
        ).scalar()
    return {"executions": int(count or 0), "status": status}


def init() -> dict:
    ensure_repo()
    ctx, _service, review, research = context()
    project = Project(id=new_id(), name=f"phase25-{new_id()[:8]}", repo_path=str(REPO), description="Phase 2.5 disposable coding fixture")
    with session_scope(ctx.sessions) as session:
        save_project(session, project)
    started = research.start(project.id, REQUIREMENT, {"max_files": 20})
    planned = PlanningWorkflowService(ctx, MemorySaver()).start(started["workflow_id"], started["research_artifact_id"])
    status = review.resolution_status(started["workflow_id"])
    answers = []
    if status["blocking_open"]:
        for item in status["clarifications"]:
            if item["blocking"] and item["state"] != "answered":
                answers.append(review.answer(started["workflow_id"], item["clarification_id"], ANSWER))
        planned = review.revise(started["workflow_id"])
    current = review.current_version(started["workflow_id"])
    artifact = None
    with session_scope(ctx.sessions) as session:
        artifact = get_artifact(session, current["plan_artifact_id"])
    plan = Plan.model_validate(json.loads(artifact.body)["content"])
    approved = review.decide(
        started["workflow_id"],
        "approved",
        "Phase 2.5 fixture behavior is fully specified",
        current["plan_artifact_id"],
        plan_hash(plan),
    )
    task = _implementation_task(plan)
    state = {
        "project_id": project.id,
        "workflow_id": started["workflow_id"],
        "research_agent_run_id": started["agent_run_id"],
        "research_artifact_id": started["research_artifact_id"],
        "planning_agent_run_id": planned.get("agent_run_id"),
        "plan_id": plan.plan_id,
        "plan_artifact_id": current["plan_artifact_id"],
        "plan_version": current["plan_version"],
        "plan_hash": plan_hash(plan),
        "approval_id": approved.get("approval_id") or approved.get("id"),
        "approval": approved,
        "task_id": task.task_id,
        "task_title": task.title,
        "task_files": list(task.related_files),
        "clarification_answers": answers,
        "calculator_before": calculator_guard(),
    }
    save_state(state)
    print(json.dumps({k: state[k] for k in ("workflow_id", "plan_id", "plan_version", "task_id", "task_title")}, ensure_ascii=False))
    return state


def _implementation_task(plan: Plan):
    for task in plan.tasks:
        if task.task_type.value == "implementation" and any("string_utils" in item for item in task.related_files):
            return task
    for task in plan.tasks:
        if task.task_type.value == "implementation":
            return task
    raise SystemExit("planning did not produce an implementation task")


def code() -> None:
    state = load_state()
    ctx, service, _review, _research = context()
    info = ctx.git.create_workspace(state["project_id"], state["task_id"], str(REPO), "HEAD")
    state["workspace"] = info.model_dump()
    save_state(state)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=info.path, check=True, capture_output=True, text=True).stdout.strip()
    result = service.start_task(state["workflow_id"], state["task_id"], info.path, revision)
    state["coding_result"] = result
    save_state(state)
    print(json.dumps({"execution_id": result.get("execution_id"), "status": result.get("status"), "summary": result.get("summary")}, ensure_ascii=False))


def execution_row(workflow_id: str) -> dict | None:
    with engine().connect() as connection:
        row = connection.execute(
            text(
                "select id, status, conversation_id, container_id, error, attempt from executions "
                "where workflow_id = :id order by created_at desc limit 1"
            ),
            {"id": workflow_id},
        ).mappings().first()
    return None if row is None else dict(row)


def event_count(execution_id: str) -> int:
    with engine().connect() as connection:
        return int(
            connection.execute(
                text("select count(*) from execution_events where execution_id = :id"),
                {"id": execution_id},
            ).scalar()
            or 0
        )


def run_pytest() -> dict:
    state = load_state()
    ctx, _service, _review, _research = context()
    info = ctx.git.create_workspace(state["project_id"], state["task_id"], str(REPO), "HEAD")
    row = execution_row(state["workflow_id"])
    handle = ctx.containers.start(Path(info.path))
    result = ctx.sandbox.run_pytest(Path(info.path), row["id"], handle.container_id)
    with session_scope(ctx.sessions) as session:
        save_test_result(session, result)
    payload = {
        "id": result.id,
        "passed": result.passed,
        "exit_code": result.exit_code,
        "command": result.command,
        "output": result.output[-4000:],
        "passed_count": result.passed_count,
        "failed_count": result.failed_count,
    }
    state["test_result"] = payload
    save_state(state)
    print(json.dumps({"passed": result.passed, "exit_code": result.exit_code, "id": result.id}, ensure_ascii=False))
    return payload


def run_evaluate() -> dict:
    if "--wait" in sys.argv:
        time.sleep(30)
    state = load_state()
    ctx, service, _review, _research = context()
    from ase.agents.lightweight import evaluate
    from ase.contracts.domain import WorkspaceInfo
    from ase.store import save_evaluation
    from ase.workflow.graph import _deliver
    from ase.workflow.runtime import bind_context

    info = WorkspaceInfo.model_validate(state["workspace"])
    diff = ctx.git.get_diff(info)
    passed = bool(state.get("test_result", {}).get("passed"))
    report = evaluate(state["workflow_id"], passed, diff)
    with session_scope(ctx.sessions) as session:
        save_evaluation(session, report)
    bind_context(ctx)
    _deliver(
        {
            "project_id": state["project_id"],
            "workflow_id": state["workflow_id"],
            "execution_id": execution_row(state["workflow_id"])["id"],
            "workspace": info.model_dump(),
            "evaluation": report.model_dump(),
            "test_passed": passed and report.passed,
            "diff": diff,
            "container_id": execution_row(state["workflow_id"]).get("container_id"),
        }
    )
    if report.passed and passed:
        service.complete_task(state["workflow_id"], state["task_id"], execution_row(state["workflow_id"])["id"])
    state["evaluation"] = {"id": report.id, "passed": report.passed, "summary": report.summary, "findings": report.findings}
    state["diff"] = diff
    save_state(state)
    print(json.dumps(state["evaluation"], ensure_ascii=False))
    return state["evaluation"]


def accept() -> None:
    state = init() if not STATE_PATH.exists() else load_state()
    before = execution_row(state["workflow_id"])
    child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "code"], cwd=r"E:\demo\Axiom")
    observed = _wait_and_kill(child, state["workflow_id"])
    state = load_state()
    state["interrupt"] = observed
    save_state(state)
    time.sleep(3)
    resumed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "code"], cwd=r"E:\demo\Axiom")
    state = load_state()
    state["resume_exit_code"] = resumed.returncode
    after = execution_row(state["workflow_id"])
    state["execution_after_resume"] = after
    state["execution_before_interrupt"] = before
    save_state(state)
    if resumed.returncode != 0 or after is None or after["status"] not in {"completed", "succeeded"}:
        raise SystemExit(f"coding did not complete: {after}")
    if not _file_changed():
        raise SystemExit("model finished without modifying src/string_utils.py")
    if not _tests_unchanged():
        raise SystemExit("tests/test_string_utils.py was modified")
    test = run_pytest()
    waiter = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "evaluate", "--wait"], cwd=r"E:\demo\Axiom")
    time.sleep(2)
    _stop(waiter)
    evaluation = run_evaluate()
    state = load_state()
    state["calculator_after"] = calculator_guard()
    porcelain = subprocess.run(["git", "status", "--porcelain", "--", "ase-poc-demo/calculator"], cwd=r"E:\demo\Axiom", capture_output=True, text=True)
    state["calculator_git_status"] = porcelain.stdout
    main_status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, capture_output=True, text=True)
    state["fixture_main_status"] = main_status.stdout
    state["usages"] = _usages(state["workflow_id"])
    state["evaluation"] = evaluation
    state["test_result"] = test
    state["event_count"] = event_count(after["id"])
    save_state(state)
    print(json.dumps({"execution": after["id"], "conversation": after["conversation_id"], "test": test["id"], "evaluation": evaluation}, ensure_ascii=False))


def _stop(child: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(child.pid)], check=False, capture_output=True)
    try:
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()


def _wait_and_kill(child: subprocess.Popen, workflow_id: str) -> dict:
    deadline = time.time() + 900
    seen = None
    first_event_at = None
    while time.time() < deadline:
        if child.poll() is not None:
            return {"killed": False, "exit_code": child.returncode, "reason": "coding exited before interrupt"}
        row = execution_row(workflow_id)
        if row and row.get("conversation_id"):
            seen = {"id": row["id"], "conversation_id": row["conversation_id"], "status": row["status"]}
            events = event_count(row["id"])
            changed = _file_changed()
            if events >= 1 and first_event_at is None:
                first_event_at = time.time()
            if events >= 1 and changed:
                _stop(child)
                return {"killed": True, "scenario": "B", "execution_id": row["id"], "conversation_id": row["conversation_id"], "events": events}
            if first_event_at is not None and time.time() - first_event_at > 15 and not changed:
                _stop(child)
                return {"killed": True, "scenario": "A", "execution_id": row["id"], "conversation_id": row["conversation_id"], "events": events}
        time.sleep(2)
    if child.poll() is None:
        _stop(child)
    return {"killed": True, "scenario": "timeout", "execution": seen}


def _tests_unchanged() -> bool:
    state = load_state()
    workspace = state.get("workspace") or {}
    path = Path(workspace.get("path") or "") / "tests" / "test_string_utils.py"
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    return 'normalize_text("  Hello   WORLD  ") == "hello world"' in text and "NotImplementedError" not in text


def _file_changed() -> bool:
    state = load_state()
    workspace = state.get("workspace") or {}
    path = Path(workspace.get("path") or "") / "src" / "string_utils.py"
    if not path.exists():
        return False
    return "NotImplementedError" not in path.read_text(encoding="utf-8")


def _usages(workflow_id: str) -> list[dict]:
    with engine().connect() as connection:
        rows = connection.execute(
            text(
                "select agent_run_id, model, prompt_tokens, completion_tokens, total_tokens, latency_seconds "
                "from model_usages where workflow_id = :id"
            ),
            {"id": workflow_id},
        ).mappings()
        return [dict(row) for row in rows]


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "accept"
    if command == "init":
        init()
    elif command == "code":
        code()
    elif command == "pytest":
        run_pytest()
    elif command == "evaluate":
        run_evaluate()
    elif command == "accept":
        accept()
    else:
        raise SystemExit(f"unknown command {command}")


if __name__ == "__main__":
    main()
