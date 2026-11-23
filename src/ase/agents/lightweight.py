from ase.contracts.domain import (
    DevelopmentPlan,
    EvaluationReport,
    RequirementAnalysis,
    Task,
    TaskDependency,
    TaskStatus,
)


def analyze_requirement(requirement_id: str, statement: str) -> RequirementAnalysis:
    return RequirementAnalysis(
        requirement_id=requirement_id,
        summary=statement.strip(),
        acceptance=[
            "表达式中的科学计数法可以被计算",
            "新增行为有 pytest 覆盖",
            "改动只出现在任务分支",
        ],
        risks=["裸 eval 会执行任意代码，实现必须限制语法"],
    )


def plan_tasks(requirement_id: str, analysis: RequirementAnalysis) -> DevelopmentPlan:
    implement = Task(
        plan_id="",
        title="实现科学计数法表达式计算",
        instruction=(
            analysis.summary
            + "\n请先阅读 /workspace 中的 calculator.py 和 test_calculator.py。"
            + "现有测试把 NotImplementedError 当成通过，必须实现 evaluate，"
            + "并把测试改成对科学计数法、运算优先级、括号和浮点数的真实断言。"
            + "不要使用 eval、exec 或 subprocess。"
        ),
        status=TaskStatus.READY,
        agent_id="coding",
    )
    verify = Task(
        plan_id="",
        title="补充并通过单元测试",
        instruction="运行 pytest，失败时只根据诊断说明修复测试或实现。",
        status=TaskStatus.PENDING,
        agent_id="testing",
    )
    plan = DevelopmentPlan(requirement_id=requirement_id, summary=analysis.summary, tasks=[implement, verify], dependencies=[])
    implement.plan_id = plan.id
    verify.plan_id = plan.id
    plan.dependencies = [
        TaskDependency(plan_id=plan.id, predecessor_id=implement.id, successor_id=verify.id)
    ]
    return plan


def diagnose(output: str) -> str:
    head = output.strip().splitlines()[-8:]
    return "测试未通过。根据日志修复实现，不要扩大改动范围。\n" + "\n".join(head)


def evaluate(workflow_id: str, passed: bool, diff: str) -> EvaluationReport:
    findings = []
    if "main" in diff and "merge" in diff.lower():
        findings.append("diff 中出现合并到主分支的痕迹")
    if not diff.strip():
        findings.append("没有产生代码差异")
    return EvaluationReport(
        workflow_id=workflow_id,
        passed=passed and not findings,
        summary="测试通过且存在代码差异" if passed and not findings else "交付未满足验收",
        findings=findings,
    )
