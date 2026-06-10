"""Database layer: ORM models, async session, repositories.

Note: ``db.models`` requires the ``postgresql`` dialect (JSONB) at *runtime*
against Postgres, but importing the models does not need a live DB.
"""

from db.session import (
    dispose_engine,
    get_engine,
    get_session,
    get_sessionmaker,
    session_scope,
)

__all__ = [
    "get_engine",
    "get_sessionmaker",
    "get_session",
    "session_scope",
    "dispose_engine",
]
