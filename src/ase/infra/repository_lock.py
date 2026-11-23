import os
import subprocess
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ase.contracts.domain import new_id, utcnow
from ase.db.orm import RepositoryLeaseRow
from ase.errors import WorkflowConflict


def repository_identity(path: Path) -> str:
    resolved = path.resolve()
    completed = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=resolved,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"{resolved} is not a git repository")
    common = Path(completed.stdout.strip())
    if not common.is_absolute():
        common = (resolved / common).resolve()
    else:
        common = common.resolve()
    identity = str(common)
    if os.name == "nt":
        identity = os.path.normcase(identity)
    return identity


def acquire_repository_lease(
    session: Session,
    *,
    repository_key: str,
    holder_id: str,
    workflow_id: str,
    project_id: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> None:
    current = now or utcnow()
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": zlib.crc32(repository_key.encode())},
        )
    row = session.scalar(select(RepositoryLeaseRow).where(RepositoryLeaseRow.repository_key == repository_key))
    expiry = current + timedelta(seconds=ttl_seconds)
    if row is None:
        session.add(
            RepositoryLeaseRow(
                id=new_id(),
                repository_key=repository_key,
                holder_id=holder_id,
                workflow_id=workflow_id,
                project_id=project_id,
                expires_at=expiry,
                created_at=current,
                updated_at=current,
            )
        )
        session.flush()
        return
    if _aware(row.expires_at) <= _aware(current):
        row.holder_id = holder_id
        row.workflow_id = workflow_id
        row.project_id = project_id
        row.expires_at = expiry
        row.updated_at = current
        return
    if row.holder_id == holder_id:
        row.expires_at = expiry
        row.updated_at = current
        return
    raise WorkflowConflict(f"repository is locked by {row.holder_id}")


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def release_repository_lease(session: Session, repository_key: str, holder_id: str) -> None:
    row = session.scalar(select(RepositoryLeaseRow).where(RepositoryLeaseRow.repository_key == repository_key))
    if row is None:
        return
    if row.holder_id != holder_id:
        raise WorkflowConflict("repository lock is held by a different owner")
    session.delete(row)
