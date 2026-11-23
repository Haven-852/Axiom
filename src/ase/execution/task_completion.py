"""A coding run is not a completed task until tests and evaluation agree."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from ase.contracts.domain import ExecutionStatus
from ase.db.orm import EvaluationReportRow, TestResultRow


def assess(session: Session, *, workflow_id: str, execution_id: str, execution_status: str) -> dict:
    if execution_status not in {ExecutionStatus.COMPLETED.value, ExecutionStatus.SUCCEEDED.value}:
        return {"complete": False, "test_status": "unverified", "test_result_ids": [], "reason": "execution did not complete"}
    rows = session.scalars(select(TestResultRow).where(TestResultRow.execution_id == execution_id)).all()
    if not rows:
        return {"complete": False, "test_status": "unverified", "test_result_ids": [], "reason": "no test result"}
    ids = [row.id for row in rows]
    if any(not row.passed for row in rows):
        return {"complete": False, "test_status": "failed", "test_result_ids": ids, "reason": "tests failed"}
    evaluation = session.scalar(select(EvaluationReportRow).where(EvaluationReportRow.workflow_id == workflow_id))
    if evaluation is None:
        return {"complete": False, "test_status": "unverified", "test_result_ids": ids, "reason": "evaluation is missing"}
    if not evaluation.passed:
        return {"complete": False, "test_status": "automated_verified", "test_result_ids": ids, "reason": "evaluation failed"}
    return {"complete": True, "test_status": "automated_verified", "test_result_ids": ids, "reason": ""}
