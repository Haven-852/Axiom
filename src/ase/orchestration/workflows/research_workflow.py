from ase.orchestration.graph import build_orchestration_graph


def research_echo_workflow(checkpointer=None, interrupt_before=None):
    """Serial fixture: ResearchStubAgent then EchoAgent. Not a product workflow."""

    return build_orchestration_graph(checkpointer, interrupt_before=interrupt_before)
