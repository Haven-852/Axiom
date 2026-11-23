from ase.contracts.domain import DevelopmentPlan, RequirementAnalysis, Task, TaskDependency, TaskStatus


def test_models_round_trip() -> None:
    analysis = RequirementAnalysis(requirement_id="req", summary="计算 1e2", acceptance=["覆盖科学计数法"])
    task = Task(plan_id="plan", title="实现", instruction="实现", status=TaskStatus.READY, agent_id="coding")
    other = Task(plan_id="plan", title="测试", instruction="测试", agent_id="testing")
    plan = DevelopmentPlan(
        requirement_id="req",
        summary="plan",
        tasks=[task, other],
        dependencies=[TaskDependency(plan_id="plan", predecessor_id=task.id, successor_id=other.id)],
    )
    restored = DevelopmentPlan.model_validate_json(plan.model_dump_json())
    assert restored.tasks[0].status is TaskStatus.READY
    assert restored.dependencies[0].successor_id == other.id
    assert restored.model_dump()["id"] == plan.id
