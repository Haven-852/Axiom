from dataclasses import replace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.exc import OperationalError

from ase.errors import WorkflowConflict

from ase.adapters.openhands import OpenHandsCodingAdapter
from ase.adapters.scripted import ScriptedCodingAdapter
from ase.agents.registry import default_agents
from ase.config import Settings
from ase.db.session import make_session_factory, session_scope
from ase.infra.agent_container import AgentContainerManager
from ase.infra.docker_sandbox import DockerSandbox
from ase.infra.git_workspace import GitWorkspaceManager
from ase.store import save_agent_spec
from ase.workflow.graph import build_graph
from ase.workflow.runtime import AppContext
from ase.workflow.service import WorkflowService


def build_context(settings: Settings, coding=None, checkpointer=None, sandbox=None) -> AppContext:
    sessions = make_session_factory(settings)
    containers = AgentContainerManager(settings)
    if coding is None:
        coding = (
            OpenHandsCodingAdapter(settings, containers=containers)
            if settings.coding_runtime == "openhands"
            else ScriptedCodingAdapter()
        )
    graph = build_graph(checkpointer or _checkpointer(settings))
    ctx = AppContext(
        settings=settings,
        sessions=sessions,
        git=GitWorkspaceManager(settings),
        containers=containers,
        sandbox=sandbox or DockerSandbox(settings, containers),
        coding=coding,
        graph=graph,
    )
    with session_scope(sessions) as session:
        for spec in default_agents():
            save_agent_spec(
                session,
                spec.agent_id,
                spec.name,
                spec.agent_type,
                spec.description,
                spec.model_dump(mode="json"),
            )
    return ctx


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    from ase.api.router import api_router

    settings = settings or Settings()
    ctx = ctx or build_context(settings)
    app = FastAPI(title="ASE", version="0.1.0")
    from ase.orchestration.research_service import ResearchWorkflowService
    from ase.planning.service import PlanningWorkflowService

    app.state.ctx = ctx
    app.state.workflows = WorkflowService(ctx)
    app.state.research = ResearchWorkflowService(ctx, checkpointer=_checkpointer(settings))
    app.state.planning = PlanningWorkflowService(ctx, checkpointer=_checkpointer(settings))
    from ase.planning.review_service import PlanReviewService

    app.state.plan_review = PlanReviewService(app.state.planning)
    from ase.agents.coding_agent import CodingAgent, coding_definition
    from ase.agents.registry import AgentRegistry
    from ase.agents.runner import AgentRunner
    from ase.execution.coding_service import CodingTaskService

    coding_registry = AgentRegistry()
    coding_registry.register(coding_definition(), lambda: CodingAgent(ctx.coding, ctx.sessions))
    app.state.coding_tasks = CodingTaskService(
        ctx,
        AgentRunner(coding_registry, ctx.sessions, settings),
        checkpointer=_checkpointer(settings),
    )
    app.include_router(api_router, prefix="/api/v1")

    @app.exception_handler(PermissionError)
    async def permission_error(_, exc: PermissionError):
        return JSONResponse(status_code=403, content={"ok": False, "error": str(exc)})

    @app.exception_handler(WorkflowConflict)
    async def workflow_conflict(_, exc: WorkflowConflict):
        return JSONResponse(status_code=409, content={"ok": False, "error": str(exc)})

    @app.exception_handler(OperationalError)
    async def database_unavailable(_, exc: OperationalError):
        del exc
        return JSONResponse(status_code=503, content={"ok": False, "error": "database unavailable"})

    @app.exception_handler(RuntimeError)
    async def runtime_error(_, exc: RuntimeError):
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    return app


def replace_coding(ctx: AppContext, coding) -> AppContext:
    return replace(ctx, coding=coding)


def _checkpointer(settings: Settings):
    backend = settings.checkpoint_backend
    if backend == "auto":
        backend = "postgres" if settings.database_url.startswith("postgres") else "sqlite"
    if backend == "postgres":
        return _postgres_checkpointer(settings)
    return _sqlite_checkpointer(settings)


def _postgres_checkpointer(settings: Settings):
    from psycopg import Connection
    from psycopg.rows import dict_row
    from langgraph.checkpoint.postgres import PostgresSaver

    url = settings.database_url.replace("postgresql+psycopg", "postgresql", 1)
    connection = Connection.connect(url, autocommit=True, prepare_threshold=0, row_factory=dict_row)
    saver = PostgresSaver(connection)
    saver.setup()
    return saver


def _sqlite_checkpointer(settings: Settings):
    path = settings.workspace_root.parent / "checkpoints.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver

        connection = sqlite3.connect(str(path), check_same_thread=False)
        saver = SqliteSaver(connection)
        setup = getattr(saver, "setup", None)
        if setup is not None:
            setup()
        return saver
    except Exception:
        return MemorySaver()
