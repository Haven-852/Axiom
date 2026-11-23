from langgraph.graph import END, START, StateGraph

from ase.orchestration.research_nodes import research_report_node
from ase.orchestration.state import AgentWorkflowState


def build_research_graph(checkpointer=None, interrupt_before=None):
    graph = StateGraph(AgentWorkflowState)
    graph.add_node("research", research_report_node)
    graph.add_edge(START, "research")
    graph.add_edge("research", END)
    return graph.compile(checkpointer=checkpointer, interrupt_before=interrupt_before or [])
