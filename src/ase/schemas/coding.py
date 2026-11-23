from pydantic import BaseModel, Field


class CodingInput(BaseModel):
    workflow_id: str
    plan_id: str
    plan_version: int
    plan_artifact_id: str
    plan_hash: str
    task_id: str
    task_description: str
    task_type: str
    priority: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    related_files: list[str] = Field(default_factory=list)
    source_findings: list[str] = Field(default_factory=list)
    source_research_artifact_id: str = ""
    dependencies: list[str] = Field(default_factory=list)
    repository_revision: str = ""
    execution_constraints: list[str] = Field(default_factory=list)
    instruction: str
    workspace_path: str
    user_decisions: list[dict] = Field(default_factory=list)


class CodingResult(BaseModel):
    task_id: str
    execution_id: str
    agent_run_id: str
    status: str
    changed_files: list[str] = Field(default_factory=list)
    summary: str = ""
    test_status: str = "unverified"
    test_result_ids: list[str] = Field(default_factory=list)
    diagnosis: str = ""
    repair_attempts: int = 0
    unresolved_issues: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    commit_hash: str = ""
    failure_reason: str = ""
