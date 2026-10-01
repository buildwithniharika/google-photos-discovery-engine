"""Database engine and sessions. SQLite locally, hosted Postgres when DATABASE_URL is set."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from discovery.models.orm import CORE_TABLES, Base

log = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    """Route Postgres URLs to the psycopg (v3) driver, which is the one we install."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def redact_url(url: str) -> str:
    return make_url(normalize_url(url)).render_as_string(hide_password=True)


def make_engine(url: str) -> Engine:
    url = normalize_url(url)
    parsed = make_url(url)

    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine

    # Hosted Postgres: small pool (free-tier connection limits), pre-ping for idle
    # disconnects, generous connect timeout because free-tier databases sleep.
    return create_engine(
        url,
        pool_size=3,
        max_overflow=2,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": 30},
    )


@retry(
    retry=retry_if_exception_type(OperationalError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, max=20),
    reraise=True,
)
def wait_for_db(engine: Engine) -> None:
    """First connection to a suspended free-tier database can take a while."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def init_db(engine: Engine) -> list[str]:
    """Create any missing tables (idempotent). Returns the core tables now present."""
    wait_for_db(engine)
    Base.metadata.create_all(engine)
    present = set(inspect(engine).get_table_names())
    missing = CORE_TABLES - present
    if missing:
        raise RuntimeError(f"Tables missing after create_all: {sorted(missing)}")
    return sorted(CORE_TABLES)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


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
