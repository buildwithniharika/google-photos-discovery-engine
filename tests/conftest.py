from __future__ import annotations

import os

import pytest
from sqlalchemy import Engine

from discovery.config import AppConfig, load_config
from discovery.db import init_db, make_engine, make_session_factory
from discovery.models.orm import Base


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never read the developer's .env or hit real services."""
    monkeypatch.setattr("discovery.config.load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AUTHOR_HASH_SALT", raising=False)


@pytest.fixture
def cfg() -> AppConfig:
    return load_config()


def _db_params() -> list:
    params = [pytest.param("sqlite", id="sqlite")]
    params.append(
        pytest.param(
            "postgres",
            id="postgres",
            marks=pytest.mark.skipif(
                not os.environ.get("TEST_DATABASE_URL"),
                reason="TEST_DATABASE_URL not set (Postgres tests run in CI)",
            ),
        )
    )
    return params


@pytest.fixture(params=_db_params())
def engine(request: pytest.FixtureRequest, tmp_path) -> Engine:
    if request.param == "sqlite":
        eng = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    else:
        eng = make_engine(os.environ["TEST_DATABASE_URL"])
        Base.metadata.drop_all(eng)
    init_db(eng)
    yield eng
    if request.param == "postgres":
        Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def sqlite_engine(tmp_path) -> Engine:
    eng = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(sqlite_engine):
    return make_session_factory(sqlite_engine)
