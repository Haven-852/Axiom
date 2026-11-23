from langgraph.graph import END, START, StateGraph

from ase.orchestration.planning_nodes import planning_node
from ase.orchestration.state import AgentWorkflowState


def build_planning_graph(checkpointer=None, interrupt_before=None):
    graph = StateGraph(AgentWorkflowState)
    graph.add_node("planning", planning_node)
    graph.add_edge(START, "planning")
    graph.add_edge("planning", END)
    return graph.compile(checkpointer=checkpointer, interrupt_before=interrupt_before or [])
