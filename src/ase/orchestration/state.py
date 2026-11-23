from typing import TypedDict


class AgentWorkflowState(TypedDict, total=False):
    workflow_id: str
    project_id: str | None
    requirement: str
    current_agent: str | None
    agent_run_ids: list[str]
    artifact_ids: list[str]
    execution_ids: list[str]
    errors: list[dict]
    status: str
    research_output: dict
    echo_output: dict
    research_report: dict
    research_key: str
    include_tests: bool
    include_dependencies: bool
    max_files: int
    max_context_chars: int
    research_artifact_id: str
    planning_key: str
    plan: dict
    planning_decisions: list
    parent_plan_artifact_id: str
    parent_clarifications: list
    parent_tasks: list
    clarification_revision: str
    user_change_hash: str
    deferred_questions: list
