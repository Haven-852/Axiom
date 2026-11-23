from langgraph.graph import END, START, StateGraph

from ase.orchestration.nodes import echo_node, research_node
from ase.orchestration.routing import next_after_research
from ase.orchestration.state import AgentWorkflowState


def build_orchestration_graph(checkpointer=None, interrupt_before=None):
    graph = StateGraph(AgentWorkflowState)
    graph.add_node("research_stub", research_node)
    graph.add_node("echo", echo_node)
    graph.add_edge(START, "research_stub")
    graph.add_conditional_edges("research_stub", next_after_research, {"echo": "echo", "end": END})
    graph.add_edge("echo", END)
    return graph.compile(checkpointer=checkpointer, interrupt_before=interrupt_before or [])
