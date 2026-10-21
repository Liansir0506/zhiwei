"""One shared MySQL lock serializes ingestion mutations and publication."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from inspect import signature
import logging

import sqlalchemy as sa
from fastapi import HTTPException

from qa_core.config.settings import get_settings
from qa_core.quality.publication import validate_version

_held = ContextVar("candidate_write_guard", default=None)
LOCK_NAME = "rag_kb_publication"


class WriteBusy(HTTPException):
    def __init__(self):
        super().__init__(409, "入库或发布进行中，请稍后重试")


@contextmanager
def candidate_write(version, *, settings=None, invalidate_report=True):
    settings = settings or get_settings()
    try:
        validate_version(version)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    identity = (settings.mysql_dsn, version)
    if _held.get() is not None:
        if _held.get() != identity:
            raise HTTPException(409, "不能在同一写入操作中切换知识版本")
        yield
        return
    engine = sa.create_engine(settings.mysql_dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            acquired = conn.execute(sa.text("SELECT GET_LOCK(:name, 0)"), {"name": LOCK_NAME}).scalar()
            conn.commit()
            if acquired != 1:
                raise WriteBusy()
            token = None
            try:
                with conn.begin():
                    conn.execute(sa.text("INSERT IGNORE INTO kb_versions (version, scenario, status) "
                                         "VALUES (:v, :s, 'STAGED')"),
                                 {"v": version, "s": settings.scenario})
                    status = conn.execute(sa.text("SELECT status FROM kb_versions WHERE version=:v FOR UPDATE"),
                                          {"v": version}).scalar()
                    if status != "STAGED":
                        raise HTTPException(409, "仅允许修改 STAGED 候选；ACTIVE/ARCHIVED 不可写")
                    if invalidate_report:
                        conn.execute(sa.text("UPDATE kb_versions SET quality_report_path=NULL WHERE version=:v"),
                                     {"v": version})
                token = _held.set(identity)
                yield
            finally:
                if token is not None:
                    _held.reset(token)
                try:
                    conn.execute(sa.text("SELECT RELEASE_LOCK(:name)"), {"name": LOCK_NAME})
                    conn.commit()
                except Exception:
                    conn.invalidate()
                    logging.getLogger(__name__).exception("candidate write lock cleanup failed")
    finally:
        engine.dispose()


def guarded_write(func):
    params = signature(func)
    @wraps(func)
    def wrapped(*args, **kwargs):
        version = params.bind(*args, **kwargs).arguments["kb_version"]
        with candidate_write(version):
            return func(*args, **kwargs)
    return wrapped
