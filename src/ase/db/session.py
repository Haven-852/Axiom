from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ase.config import Settings
from ase.db.orm import Base


def make_engine(settings: Settings):
    connect_args = {}
    kwargs = {"pool_pre_ping": True, "pool_timeout": settings.db_pool_timeout, "future": True}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    else:
        connect_args["connect_timeout"] = settings.db_connect_timeout
    return create_engine(settings.database_url, connect_args=connect_args, **kwargs)


def make_session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = make_engine(settings)
    if settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
