from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ase.adapters.coding import CodingAgentAdapter
from ase.config import Settings
from ase.infra.agent_container import AgentContainerManager
from ase.infra.docker_sandbox import DockerSandbox
from ase.infra.git_workspace import GitWorkspaceManager


@dataclass
class AppContext:
    settings: Settings
    sessions: sessionmaker[Session]
    git: GitWorkspaceManager
    containers: AgentContainerManager
    sandbox: DockerSandbox
    coding: CodingAgentAdapter
    graph: Any


_CONTEXT: ContextVar[AppContext | None] = ContextVar("ase_context", default=None)


def current_context() -> AppContext:
    ctx = _CONTEXT.get()
    if ctx is None:
        raise RuntimeError("workflow context is not set")
    return ctx


def bind_context(ctx: AppContext):
    return _CONTEXT.set(ctx)


def reset_context(token) -> None:
    _CONTEXT.reset(token)
