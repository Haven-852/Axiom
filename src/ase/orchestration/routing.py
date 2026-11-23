def next_after_research(state: dict) -> str:
    if state.get("errors"):
        return "end"
    return "echo"
