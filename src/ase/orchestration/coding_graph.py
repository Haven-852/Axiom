"""Controlled coding thread. It is not the production coding graph."""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph


class CodingGraphState(TypedDict, total=False):
    workflow_id: str
    task_id: str
    workspace_path: str
    repository_revision: str
    result: dict
    error: str


def build_coding_graph(service, checkpointer: Any):
    graph = StateGraph(CodingGraphState)

    def gate(state: CodingGraphState) -> dict:
        from ase.execution.eligibility import EligibilityError

        task_id = state.get("task_id") or service.next_task(state["workflow_id"])
        if not task_id:
            return {"error": "no approved task is ready"}
        try:
            service.eligibility(state["workflow_id"], task_id)
        except EligibilityError as exc:
            if exc.code != "task_not_ready":
                raise
        return {"task_id": task_id}

    def code(state: CodingGraphState) -> dict:
        if state.get("error"):
            return {}
        result = service._execute(
            state["workflow_id"],
            state["task_id"],
            state["workspace_path"],
            state.get("repository_revision") or "",
        )
        return {"result": result}

    graph.add_node("eligibility", gate)
    graph.add_node("coding", code)
    graph.add_edge(START, "eligibility")
    graph.add_edge("eligibility", "coding")
    graph.add_edge("coding", END)
    return graph.compile(checkpointer=checkpointer)
